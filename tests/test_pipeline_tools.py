"""The real tools: make, uvx pre-commit, Go and scc.

These tests carry the `network` marker. The first run downloads pre-commit, Go and scc into the
default uv and pre-commit caches of the user, and later runs reuse them.
"""

import os
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from conftest import GIT_ENV, FixtureTemplate, git
from pipeline_support import BIG, Project, Projects, code


@pytest.fixture(scope="module")
def projects(tmp_path_factory: pytest.TempPathFactory) -> Projects:
    template = FixtureTemplate(tmp_path_factory.mktemp("template") / "src")
    template.release("v0.1.0")
    return Projects(template, tmp_path_factory.mktemp("projects"))


@pytest.fixture
def project(projects: Projects) -> Iterator[Callable[[str], Project]]:
    """Hand out a repo by language set. Each repo is reset to its first commit after the test."""
    handed: list[Project] = []

    def get(name: str) -> Project:
        handed.append(projects.get(name))
        return handed[-1]

    yield get
    for each in handed:
        each.reset()


@pytest.mark.network
def test_check_passes_without_a_language_and_downloads_no_go(
    project: Callable[[str], Project], tmp_path: Path
) -> None:
    cache = tmp_path / "pre-commit-home"

    result = project("none").make("check", env={"PRE_COMMIT_HOME": str(cache)})

    assert result.returncode == 0, result.stdout + result.stderr
    assert "file length" in result.stdout  # the hook ran, and had nothing to check
    assert not list(cache.rglob("golangenv*"))


@pytest.mark.network
@pytest.mark.parametrize("language", ["python", "go", "rust"])
def test_check_fails_above_the_limit_and_names_the_file(
    project: Callable[[str], Project], language: str
) -> None:
    repo = project(language)
    repo.write(BIG[language], code(language, 501))  # not committed

    result = repo.make("check")

    assert result.returncode != 0
    assert f"{BIG[language]}: 501 code lines, the limit is 500" in result.stdout


@pytest.mark.network
@pytest.mark.parametrize("language", ["python", "go", "rust"])
def test_check_passes_at_the_limit(project: Callable[[str], Project], language: str) -> None:
    repo = project(language)
    repo.write(BIG[language], code(language, 500))

    result = repo.make("check")

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.network
def test_check_does_not_look_at_a_file_that_git_ignores(project: Callable[[str], Project]) -> None:
    repo = project("python")
    repo.write(".gitignore", "build/\n")
    repo.write("build/big.py", code("python", 900))

    result = repo.make("check")

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.network
def test_a_path_that_starts_with_a_dash_is_checked_like_any_other(
    project: Callable[[str], Project],
) -> None:
    repo = project("python")
    repo.write("-dash.py", code("python", 501))

    result = repo.make("length")

    assert result.returncode != 0
    assert "-dash.py: 501 code lines, the limit is 500" in result.stdout


@pytest.mark.network
def test_hooks_installs_both_hook_types_and_a_commit_of_a_long_file_is_blocked(
    project: Callable[[str], Project],
) -> None:
    repo = project("python")

    installed = repo.make("hooks")
    repo.write("big.py", code("python", 501))
    git(repo.root, "add", "big.py")
    commit = subprocess.run(
        ["git", "commit", "-q", "-m", "feat: add a long file"],
        cwd=repo.root,
        env={**os.environ, **GIT_ENV},
        capture_output=True,
        text=True,
        check=False,
    )

    assert installed.returncode == 0, installed.stderr
    assert (repo.root / ".git" / "hooks" / "pre-commit").is_file()
    assert (repo.root / ".git" / "hooks" / "commit-msg").is_file()
    assert commit.returncode != 0
    assert "big.py: 501 code lines" in commit.stdout + commit.stderr


@pytest.mark.network
def test_a_commit_runs_the_length_hook_once_and_not_at_the_commit_msg_stage(
    project: Callable[[str], Project],
) -> None:
    repo = project("python")
    repo.make("hooks")
    repo.write("small.py", code("python", 5))
    git(repo.root, "add", "small.py")

    commit = subprocess.run(
        ["git", "commit", "-q", "-m", "feat: add a small file"],
        cwd=repo.root,
        env={**os.environ, **GIT_ENV},
        capture_output=True,
        text=True,
        check=False,
    )

    assert commit.returncode == 0, commit.stdout + commit.stderr
    assert (commit.stdout + commit.stderr).count("file length") == 1


@pytest.mark.network
def test_check_fails_for_a_committed_long_file(project: Callable[[str], Project]) -> None:
    repo = project("python")
    repo.write("big.py", code("python", 501))
    git(repo.root, "add", "big.py")
    git(repo.root, "commit", "-q", "-m", "feat: add a long file")

    result = repo.make("check")

    assert result.returncode != 0
    assert "big.py: 501 code lines, the limit is 500" in result.stdout


@pytest.mark.network
def test_hook_checks_an_executable_python_script_that_has_no_extension(
    project: Callable[[str], Project],
) -> None:
    repo = project("python")
    repo.write("bin/tool", "#!/usr/bin/env python3\n" + code("python", 501), executable=True)

    result = repo.length("bin/tool")

    assert result.returncode == 1
    assert "bin/tool: 501 code lines" in result.stdout


@pytest.mark.network
def test_check_leaves_vendored_code_alone_through_make(project: Callable[[str], Project]) -> None:
    # make gives pre-commit paths that start with `./`, and the exclude pattern must match them.
    repo = project("mixed")
    repo.write("vendor/x/y.go", code("go", 501))
    repo.write("sub/vendor/z.rs", code("rust", 501))
    repo.write("pkg/_vendor/w.py", code("python", 501))

    result = repo.make("check")

    assert result.returncode == 0, result.stdout + result.stderr


# What the hook does with each kind of file. A file above the limit that the check must leave
# alone passes. A file that it must refuse fails, and its message names the file.
GENERATED_GO = "// Code generated by protoc-gen-go. DO NOT EDIT.\n"
PROTOC = "# Generated by the protocol buffer compiler.  DO NOT EDIT!\n"
DOCSTRING = '"""\n' + "doc\n" * 800 + '"""\nx = 1\n'


def leave(language: str, name: str, text: str, said: str = "") -> tuple[str, str, str, bool, str]:
    return language, name, text, True, said


def refuse(language: str, name: str, text: str, lines: int) -> tuple[str, str, str, bool, str]:
    return language, name, text, False, f"{name}: {lines} code lines"


FILES = {
    "many comments and blanks": leave("python", "c.py", "# c\n\n" * 800 + code("python", 10)),
    "a docstring is a comment": leave("python", "d.py", DOCSTRING),
    "an empty __init__": leave("python", "pkg/__init__.py", ""),
    "a lockfile": leave("python", "uv.lock", "version = 1\n" * 600),
    "a Markdown file": leave("python", "README.md", "word\n" * 600),
    "vendor": leave("go", "vendor/x/y.go", code("go", 501)),
    "a nested vendor": leave("go", "sub/vendor/y.go", code("go", 501)),
    "a generated Go file": leave(
        "go", "gen.go", GENERATED_GO + code("go", 600), "gen.go: skipped, generated"
    ),
    "a protoc file": leave(
        "python", "a_pb2.py", PROTOC + code("python", 600), "a_pb2.py: skipped, generated"
    ),
    "a Python _vendor tree": leave("python", "pkg/_vendor/x.py", code("python", 501)),
    "a language that is not detected": leave("python", "big.go", code("go", 501)),
    "a test file": refuse("python", "tests/test_big.py", code("python", 501), 501),
    "a Go test file": refuse("go", "big_test.go", code("go", 501), 501),
    "a stub file": refuse("python", "stub.pyi", code("python", 501), 501),
    "a bare do-not-edit comment": refuse(
        "python", "list.py", "# do not edit this list\n" + code("python", 600), 600
    ),
    "a path with a comma": refuse("python", "we,ird.py", code("python", 501), 501),
    "a path with a space": refuse("python", "sp ace.py", code("python", 501), 501),
}


@pytest.mark.network
@pytest.mark.parametrize("case", FILES)
def test_hook_checks_the_files_it_should_and_leaves_the_others(
    project: Callable[[str], Project], case: str
) -> None:
    language, name, text, passes, expected = FILES[case]
    repo = project(language)
    repo.write(name, text)

    result = repo.length(name)

    assert (result.returncode == 0) is passes, result.stdout + result.stderr
    assert expected in result.stdout


@pytest.mark.network
@pytest.mark.parametrize("language", ["python", "go", "rust"])
def test_hook_checks_every_detected_language_in_a_mixed_repo(
    project: Callable[[str], Project], language: str
) -> None:
    repo = project("mixed")
    repo.write(BIG[language], code(language, 501))

    result = repo.length(BIG[language])

    assert result.returncode == 1
    assert f"{BIG[language]}: 501 code lines" in result.stdout

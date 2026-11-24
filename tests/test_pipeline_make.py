"""The Makefile of the template, run with the make of this machine and a stand-in pre-commit."""

from pathlib import Path

from conftest import commit_all, make_repo
from pipeline_support import MAKEFILE, make_project, run_make, stub_pre_commit


def test_check_runs_the_targets_that_each_language_file_adds(tmp_path: Path) -> None:
    repo = make_project(tmp_path)
    (repo / "make").mkdir()
    (repo / "make" / "b.mk").write_text(
        ".PHONY: beta\nbeta:\n\t@echo beta ran\nCHECK_TARGETS += beta\n"
    )
    (repo / "make" / "a.mk").write_text(
        ".PHONY: alpha\nalpha:\n\t@echo alpha ran\nCHECK_TARGETS += alpha\n"
    )
    stub = stub_pre_commit(tmp_path)

    result = run_make(repo, "check", f"PRE_COMMIT={stub}")

    assert result.returncode == 0, result.stderr
    ran = [line for line in result.stdout.splitlines() if line.endswith(" ran")]
    assert ran == ["alpha ran", "beta ran"]
    assert "--files" in result.stdout  # the shared length target ran too


def test_check_fails_when_a_language_target_fails(tmp_path: Path) -> None:
    repo = make_project(tmp_path)
    (repo / "make").mkdir()
    (repo / "make" / "bad.mk").write_text(".PHONY: bad\nbad:\n\t@false\nCHECK_TARGETS += bad\n")

    result = run_make(repo, "check", f"PRE_COMMIT={stub_pre_commit(tmp_path)}")

    assert result.returncode != 0


def test_length_gives_pre_commit_the_files_that_git_does_not_ignore(tmp_path: Path) -> None:
    repo = make_project(tmp_path)
    (repo / ".gitignore").write_text("ignored.py\n")
    commit_all(repo)
    for name in ["new.py", "sp ace.py", "sub/dir/x.go", "ignored.py", "-o"]:
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text("x = 1\n")
    stub = stub_pre_commit(tmp_path)

    result = run_make(repo, "length", f"PRE_COMMIT={stub}")

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[:3] == ["run", "length", "--files"]
    # Tracked and untracked files. A new file is checked before its commit. Each path starts
    # with `./`, so pre-commit never reads a name such as `-o` as an option.
    assert set(lines[3:]) == {
        "./.gitignore", "./Makefile", "./a.py", "./new.py", "./sp ace.py", "./sub/dir/x.go", "./-o",
    }  # fmt: skip


def test_length_with_no_files_does_nothing(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "empty")
    (repo / "Makefile").write_text(MAKEFILE.read_text())
    (repo / ".git" / "info" / "exclude").write_text("*\n")
    stub = stub_pre_commit(tmp_path)

    result = run_make(repo, "length", f"PRE_COMMIT={stub}")

    assert result.returncode == 0, result.stderr
    assert "no files" in result.stdout
    assert not (tmp_path / "stub-pre-commit.log").exists()  # pre-commit did not run


def test_length_fails_outside_a_git_repo(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "Makefile").write_text(MAKEFILE.read_text())

    result = run_make(
        plain,
        "length",
        f"PRE_COMMIT={stub_pre_commit(tmp_path)}",
        env={"GIT_CEILING_DIRECTORIES": str(tmp_path)},
    )

    assert result.returncode != 0
    assert "not a git repository" in result.stderr
    assert not (tmp_path / "stub-pre-commit.log").exists()


def test_length_fails_when_pre_commit_fails(tmp_path: Path) -> None:
    repo = make_project(tmp_path)

    result = run_make(repo, "length", "PRE_COMMIT=false")

    assert result.returncode != 0

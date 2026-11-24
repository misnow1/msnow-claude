"""Helpers for the pipeline tests: repos with the template applied, make, and the real tools."""

import os
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import yaml
from conftest import BOOTSTRAP, GIT_ENV, REPO_ROOT, FixtureTemplate, commit_all, git, make_repo

Apply = Callable[..., subprocess.CompletedProcess[str]]

MARKERS = {"python": "pyproject.toml", "go": "go.mod", "rust": "Cargo.toml"}
TYPES = {"python": ["python", "pyi"], "go": ["go"], "rust": ["rust"]}
LANGUAGE_SETS = {
    "none": [],
    "python": ["python"],
    "go": ["go"],
    "rust": ["rust"],
    "mixed": ["python", "go", "rust"],
}


def applied_repo(
    tmp_path: Path, released: FixtureTemplate, run_apply: Apply, languages: list[str]
) -> Path:
    """A repo that has the marker file of each language, committed, with the template applied."""
    target = make_repo(tmp_path / "repo")
    for language in languages:
        (target / MARKERS[language]).write_text("")
    commit_all(target)
    result = run_apply(target, released.path)
    assert result.returncode == 0, result.stderr
    return target


def length_hook(target: Path) -> dict[str, Any]:
    config = yaml.safe_load((target / ".pre-commit-config.yaml").read_text())
    hooks = [hook for repo in config["repos"] for hook in repo["hooks"] if hook["id"] == "length"]
    assert len(hooks) == 1
    hook: dict[str, Any] = hooks[0]
    return hook


MAKEFILE = REPO_ROOT / "template" / "Makefile"
STUB = """#!/bin/sh
# A stand-in for pre-commit. It logs the call and prints each argument on its own line.
echo called >> "$0.log"
for arg; do printf '%s\\n' "$arg"; done
"""


def stub_pre_commit(root: Path) -> Path:
    stub = root / "stub-pre-commit"
    stub.write_text(STUB)
    stub.chmod(0o755)
    return stub


def run_make(
    repo: Path, *args: str, env: Mapping[str, str] | None = None, timeout: int = 60
) -> subprocess.CompletedProcess[str]:
    """Run the make of this machine in a repo. On macOS it is make 3.81."""
    return subprocess.run(
        ["make", *args],
        cwd=repo,
        env={**os.environ, **GIT_ENV, **(env or {})},
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def make_project(tmp_path: Path) -> Path:
    """A repo with the Makefile of the template and one committed file."""
    repo = make_repo(tmp_path / "project")
    (repo / "Makefile").write_text(MAKEFILE.read_text())
    (repo / "a.py").write_text("x = 1\n")
    commit_all(repo)
    return repo


# --- The real tools: make, uvx pre-commit, Go and scc ---------------------------------------


def code(language: str, lines: int) -> str:
    """Source text of a language with exactly `lines` code lines."""
    if language == "go":
        return "package p\n" + "".join(f"var x{i} = {i}\n" for i in range(lines - 1))
    if language == "rust":
        return "".join(f"const X{i}: u32 = {i};\n" for i in range(lines))
    return "".join(f"x{i} = {i}\n" for i in range(lines))


BIG = {"python": "big.py", "go": "big.go", "rust": "big.rs"}


class Project:
    """A repo that `bootstrap apply` filled, and committed. Tests add files and call make."""

    def __init__(self, root: Path, base: str) -> None:
        self.root = root
        self.base = base  # the first commit, which each test starts from

    def write(self, name: str, text: str, executable: bool = False) -> None:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        if executable:
            path.chmod(0o755)

    def make(
        self, *args: str, env: Mapping[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        # A cold cache downloads Go and builds scc on the first run.
        return run_make(self.root, *args, env=env, timeout=900)

    def pre_commit(self, *args: str) -> subprocess.CompletedProcess[str]:
        """Run the pre-commit command of the Makefile, with its pins."""
        probe = subprocess.run(
            ["make", "-s", "-f", "Makefile", "-f", "-", "show"],
            input="show:\n\t@echo $(PRE_COMMIT)\n",
            cwd=self.root,
            capture_output=True,
            text=True,
            check=True,
        )
        return subprocess.run(
            [*probe.stdout.split(), *args],
            cwd=self.root,
            env={**os.environ, **GIT_ENV},
            capture_output=True,
            text=True,
            check=False,
            timeout=900,
        )

    def length(self, *files: str) -> subprocess.CompletedProcess[str]:
        return self.pre_commit("run", "length", "--verbose", "--files", *files)

    def reset(self) -> None:
        git(self.root, "reset", "-q", "--hard", self.base)
        git(self.root, "clean", "-fdxq")
        for hook in ("pre-commit", "commit-msg"):
            (self.root / ".git" / "hooks" / hook).unlink(missing_ok=True)


class Projects:
    """Apply the template once for each set of languages, and hand the repos out."""

    def __init__(self, template: FixtureTemplate, root: Path) -> None:
        self.template = template
        self.root = root
        self.made: dict[str, Project] = {}

    def get(self, name: str) -> Project:
        if name not in self.made:
            repo = make_repo(self.root / name)
            for language in LANGUAGE_SETS[name]:
                (repo / MARKERS[language]).write_text("")
            commit_all(repo)
            result = subprocess.run(
                [str(BOOTSTRAP), "apply", str(repo), "--template", str(self.template.path)],
                env={**os.environ, **GIT_ENV},
                capture_output=True,
                text=True,
                check=False,
                stdin=subprocess.DEVNULL,
                timeout=120,
            )
            assert result.returncode == 0, result.stderr
            commit_all(repo)
            self.made[name] = Project(repo, git(repo, "rev-parse", "HEAD").strip())
        return self.made[name]

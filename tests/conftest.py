"""Shared helpers: git with a fixed identity, and a tagged fixture template."""

import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BOOTSTRAP = Path(sys.executable).parent / "bootstrap"
GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}
GIT_FLAGS = [
    "-c",
    "commit.gpgsign=false",
    "-c",
    "tag.gpgSign=false",
    "-c",
    "core.hooksPath=/dev/null",
]


def git(repo: Path, *args: str) -> str:
    """Run git in a repo with a fixed identity and no signing or hooks. Return stdout."""
    result = subprocess.run(
        ["git", *GIT_FLAGS, *args],
        cwd=repo,
        env={**os.environ, **GIT_ENV},
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def commit_all(repo: Path, message: str = "commit") -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--allow-empty", "-m", message)


def make_repo(path: Path) -> Path:
    """Create an empty git repo."""
    path.mkdir()
    git(path, "init", "-q")
    return path


def write_files(root: Path, files: Mapping[str, str | None]) -> None:
    """Write each file under root. A value of None deletes the file."""
    for name, text in files.items():
        file = root / name
        if text is None:
            file.unlink()
            continue
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text)


class FixtureTemplate:
    """A copy of this repo's template in a temp git repo. Each release is a commit and a tag."""

    def __init__(self, path: Path) -> None:
        self.path = path
        make_repo(path)
        shutil.copy(REPO_ROOT / "copier.yml", path / "copier.yml")
        shutil.copytree(REPO_ROOT / "template", path / "template")
        shutil.copytree(REPO_ROOT / "sections", path / "sections")

    def release(self, tag: str, files: Mapping[str, str | None] | None = None) -> None:
        """Change files, commit, and tag the new commit. A None value deletes a file."""
        write_files(self.path, files or {})
        commit_all(self.path, f"release {tag}")
        git(self.path, "tag", tag)


@pytest.fixture
def template(tmp_path: Path) -> FixtureTemplate:
    return FixtureTemplate(tmp_path / "template-src")


@pytest.fixture
def run_apply() -> Callable[..., subprocess.CompletedProcess[str]]:
    def run(
        target: Path,
        template: Path | str,
        *extra: str,
        env: Mapping[str, str] | None = None,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(BOOTSTRAP), "apply", str(target), "--template", str(template), *extra],
            capture_output=True,
            text=True,
            check=False,
            stdin=subprocess.DEVNULL,
            timeout=120,
            cwd=cwd,
            env={**os.environ, **GIT_ENV, **(env or {})},
        )

    return run

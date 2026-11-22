"""Shared helpers: git with a fixed identity, and a tagged fixture template."""

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

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
def released(template: FixtureTemplate) -> FixtureTemplate:
    template.release("v0.1.0")
    return template


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


FIXTURES = REPO_ROOT / "tests" / "fixtures" / "github"
SETTINGS_TEMPLATE = REPO_ROOT / "template" / ".github" / "bootstrap.yml"


def run_github(
    target: Path, *extra: str, env: Mapping[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run `bootstrap github` against a target repo."""
    return subprocess.run(
        [str(BOOTSTRAP), "github", str(target), *extra],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
        timeout=60,
        env={**os.environ, **GIT_ENV, **(env or {})},
    )


@pytest.fixture(scope="session", autouse=True)
def stub_gh(tmp_path_factory: pytest.TempPathFactory) -> None:
    """Put a `gh` that always fails on PATH, so no test reaches the real `gh`."""
    stub_dir = tmp_path_factory.mktemp("stub-gh")
    stub = stub_dir / "gh"
    stub.write_text('#!/bin/sh\necho "stub gh: pass fake_gh.env to run_github" >&2\nexit 99\n')
    stub.chmod(0o755)
    os.environ["PATH"] = f"{stub_dir}{os.pathsep}{os.environ['PATH']}"


FAKE_GH = REPO_ROOT / "tests" / "fake_gh.py"
SLUG = "octo/widgets"


class FakeGh:
    """A fake `gh` first on PATH, over a state file. Each call goes to a log."""

    def __init__(self, root: Path) -> None:
        bin_dir = root / "fake-bin"
        bin_dir.mkdir()
        wrapper = bin_dir / "gh"
        wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_GH}" "$@"\n')
        wrapper.chmod(0o755)
        self.state_path = root / "fake-gh-state.json"
        self.log_path = root / "fake-gh-log.jsonl"
        self.bin_dir = bin_dir
        self.install({})

    @property
    def env(self) -> dict[str, str]:
        return {
            "PATH": f"{self.bin_dir}{os.pathsep}{os.environ['PATH']}",
            "GH_FAKE_STATE": str(self.state_path),
            "GH_FAKE_LOG": str(self.log_path),
        }

    def install(self, state: Mapping[str, object]) -> None:
        self.state_path.write_text(json.dumps(state))

    def calls(self) -> list[dict[str, Any]]:
        if not self.log_path.exists():
            return []
        return [json.loads(line) for line in self.log_path.read_text().splitlines()]

    def writes(self) -> list[dict[str, Any]]:
        return [call for call in self.calls() if call["method"] != "GET"]

    def state(self) -> dict[str, Any]:
        loaded: dict[str, Any] = json.loads(self.state_path.read_text())
        return loaded

    def update(self, change: Callable[[dict[str, Any]], None]) -> None:
        """Change the state between two runs, for example to remove an injected error."""
        state = self.state()
        change(state)
        self.install(state)

    def clear_log(self) -> None:
        self.log_path.unlink(missing_ok=True)

    def assert_repo_scoped(self) -> None:
        """Each call is a GET, PATCH, POST or PUT under the repo, and none goes elsewhere."""
        for call in self.calls():
            endpoint = call["args"][1].partition("?")[0]
            assert call["method"] in {"GET", "PATCH", "POST", "PUT"}, call
            assert endpoint == f"repos/{SLUG}" or endpoint.startswith(f"repos/{SLUG}/"), call


def fake_state(fixture: Mapping[str, Any], admin: bool = True) -> dict[str, Any]:
    """The state of the fake GitHub for one of the state fixtures."""
    repository = fixture["repository"]
    repo = {"id": 1, "full_name": SLUG, **repository}
    if admin:
        repo["permissions"] = {"admin": True, "push": True, "pull": True}
    branches = [] if fixture.get("empty") else [{"name": repository.get("default_branch", "main")}]
    return {
        "slug": SLUG,
        "repo": repo,
        "labels": fixture["labels"],
        "branches": branches,
        "rulesets": fixture.get("rulesets", []),
    }


@pytest.fixture
def fake_gh(tmp_path: Path) -> FakeGh:
    return FakeGh(tmp_path)


def add_origin(target: Path, url: str = f"https://github.com/{SLUG}.git") -> Path:
    git(target, "remote", "add", "origin", url)
    return target


def repo_with_settings(path: Path, text: str | None = None) -> Path:
    """Create a git repo that holds a GitHub settings file and nothing else."""
    make_repo(path)
    write_files(
        path, {".github/bootstrap.yml": SETTINGS_TEMPLATE.read_text() if text is None else text}
    )
    return path

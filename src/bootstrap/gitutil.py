"""Git checks on the target repo. They run git directly, so repo config cannot hide a change."""

import os
import subprocess
from pathlib import Path

from bootstrap.errors import BootstrapError

GIT_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}


def run_git(repo: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args], cwd=repo, env=GIT_ENV, capture_output=True, text=True, check=True
        )
    except subprocess.CalledProcessError as error:
        raise BootstrapError(f"git {args[0]} failed in {repo}: {error.stderr.strip()}") from error
    return result.stdout


def check_clean_repo_root(target: Path) -> None:
    """Raise BootstrapError unless target is the root of a git repo with no uncommitted change.

    The status call lists untracked files whatever the repo config says. An update can write
    over an untracked file, and git has no copy of it.
    """
    try:
        top = run_git(target, "rev-parse", "--show-toplevel").strip()
    except BootstrapError as error:
        raise BootstrapError(f"{target} is not in a git repo. Run `git init` first.") from error
    if Path(top).resolve() != target.resolve():
        raise BootstrapError(f"{target} is not the root of its git repo, which is {top}.")
    status = run_git(
        target, "status", "--porcelain=v1", "--untracked-files=all", "--ignore-submodules=none"
    )
    if status.strip():
        raise BootstrapError(
            f"the worktree of {target} has uncommitted changes. Commit or stash them first:\n"
            + status.rstrip()
        )
    hidden = hidden_paths(target)
    if hidden:
        raise BootstrapError(
            "these files have the skip-worktree or assume-unchanged flag, so git hides their "
            "changes and an update could lose them. Clear the flag with "
            "`git update-index --no-skip-worktree --no-assume-unchanged <file>`:\n  "
            + "\n  ".join(hidden)
        )


def unmerged_paths(target: Path) -> list[str]:
    """Return the paths with an unresolved merge conflict. The -z flag avoids path quoting."""
    out = run_git(target, "diff", "--name-only", "-z", "--diff-filter=U")
    return [path for path in out.split("\0") if path]


def tracked_paths(target: Path) -> set[str]:
    """Return the paths that git tracks in target."""
    return {path for path in run_git(target, "ls-files", "-z").split("\0") if path}


def hidden_paths(target: Path) -> list[str]:
    """Return the tracked paths whose changes `git status` cannot show.

    `git ls-files -v` marks an assume-unchanged file with a lowercase tag and a skip-worktree
    file with `S`.
    """
    out = run_git(target, "ls-files", "-v", "-z")
    entries = [entry for entry in out.split("\0") if entry]
    return [entry[2:] for entry in entries if entry[0].islower() or entry[0] == "S"]


def ignored_rej_files(target: Path) -> list[str]:
    """Return the ignored `.rej` files. Copier removes a `.rej` file that it did not write."""
    out = run_git(target, "ls-files", "-z", "--others", "--ignored", "--exclude-standard")
    return [path for path in out.split("\0") if path.endswith(".rej")]

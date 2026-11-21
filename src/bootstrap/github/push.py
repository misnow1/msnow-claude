"""The branch that the plan pushes for an empty repo."""

from pathlib import Path

from bootstrap.errors import BootstrapError
from bootstrap.github.models import CurrentState
from bootstrap.gitutil import current_branch


def push_branch(target: Path, current: CurrentState) -> str | None:
    """Return the local branch to push, or None when the repo is not empty.

    The first push makes its branch the default branch on GitHub, and the ruleset then protects
    that branch. So the local branch must be the default branch that GitHub reports.
    """
    if not current.empty:
        return None
    default = current.repository.default_branch
    if default is None:
        raise BootstrapError("the state needs repository.default_branch for an empty repo")
    branch = current_branch(target)
    if branch != default:
        raise BootstrapError(
            f"the repo is empty on GitHub and its default branch is {default!r}, but the local "
            f"branch is {branch!r}. Rename it with `git branch -m {default}`, so the push does "
            f"not make {branch!r} the default branch."
        )
    return branch

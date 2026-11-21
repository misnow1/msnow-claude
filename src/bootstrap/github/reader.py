"""Read the current state of a GitHub repo through `gh`."""

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from bootstrap.errors import BootstrapError
from bootstrap.github import gh
from bootstrap.github.gh import GhError
from bootstrap.github.models import CurrentState
from bootstrap.github.repo import Repo
from bootstrap.github.settings_file import describe


def fetch(target: Path, repo: Repo, path: str, paginate: bool = False) -> Any:
    """Call `gh`. The caller handles a GhError that it expects."""
    return gh.api(target, repo, f"repos/{repo.slug}{path}", paginate)


def expect(value: Any, kind: type, what: str) -> Any:
    """Check the shape of an answer. GitHub or `gh` can return something else than the docs say."""
    if not isinstance(value, kind):
        raise BootstrapError(f"gh gave an answer of an unexpected shape for {what}")
    return value


def get(target: Path, repo: Repo, path: str, paginate: bool = False) -> Any:
    try:
        return fetch(target, repo, path, paginate)
    except GhError as error:
        raise BootstrapError(f"gh failed on {path or 'the repo'}: {error.detail}") from error


def is_empty(target: Path, repo: Repo) -> bool:
    """A repo with no branch is empty. GitHub can also answer 409 for an empty repo."""
    try:
        return bool(fetch(target, repo, "/branches?per_page=1") == [])
    except GhError as error:
        if error.status == 409 and "Git Repository is empty" in error.message:
            return True
        raise BootstrapError(f"gh failed on /branches: {error.detail}") from error


def read_rulesets(target: Path, repo: Repo) -> dict[str, Any]:
    """Read the repo rulesets, each from the detail endpoint, which has `rules` and the rest."""
    try:
        listed = fetch(target, repo, "/rulesets?includes_parents=false", paginate=True)
    except GhError as error:
        if error.status == 403 and "Upgrade to GitHub" in error.message:
            return {"rulesets_available": False}
        raise BootstrapError(f"gh failed on /rulesets: {error.detail}") from error
    rulesets: list[dict[str, Any]] = []
    unreadable: list[str] = []
    for entry in expect(listed, list, "the list of rulesets"):
        expect(entry, dict, "a ruleset in the list")
        if entry.get("source_type") != "Repository":
            continue
        if "id" not in entry or "name" not in entry:
            raise BootstrapError("gh gave a ruleset in the list with no id or name")
        detail = expect(get(target, repo, f"/rulesets/{entry['id']}"), dict, "a ruleset")
        # A caller without the right to edit rulesets gets no `bypass_actors`.
        if "bypass_actors" in detail:
            rulesets.append(detail)
        else:
            unreadable.append(entry["name"])
    return {"rulesets": rulesets, "unreadable_rulesets": unreadable}


def read_repository(target: Path, repo: Repo) -> Any:
    """Read the repo. The caller needs admin rights, or GitHub leaves out some settings."""
    try:
        repository = fetch(target, repo, "")
    except GhError as error:
        if error.status == 404:
            raise BootstrapError(f"GitHub has no repo {repo.slug}, or gh has no access") from error
        raise BootstrapError(f"gh failed on the repo: {error.detail}") from error
    permissions = expect(repository, dict, "the repo").get("permissions")
    if not isinstance(permissions, dict):
        raise BootstrapError(
            f"cannot confirm admin rights on {repo.slug}: the response has no permissions"
        )
    if permissions.get("admin") is not True:
        raise BootstrapError(f"gh needs admin rights on {repo.slug}")
    return repository


def read_state(target: Path, repo: Repo) -> CurrentState:
    data = {
        "repository": read_repository(target, repo),
        "labels": expect(
            get(target, repo, "/labels?per_page=100", paginate=True), list, "the labels"
        ),
        "empty": is_empty(target, repo),
        **read_rulesets(target, repo),
    }
    try:
        return CurrentState.model_validate(data)
    except ValidationError as error:
        raise BootstrapError(f"the state that gh read is not valid: {describe(error)}") from error

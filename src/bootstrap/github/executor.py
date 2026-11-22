"""Run a plan through `gh`, and report what happened to each operation."""

import re
from pathlib import Path
from typing import Any

from bootstrap import gitutil
from bootstrap.errors import BootstrapError
from bootstrap.github import gh
from bootstrap.github.gh import GhError
from bootstrap.github.models import (
    CreateLabel,
    CreateRuleset,
    CurrentRuleset,
    DesiredState,
    Operation,
    Plan,
    Push,
    Report,
    Result,
    UpdateRepository,
    UpdateRuleset,
)
from bootstrap.github.repo import Repo
from bootstrap.github.ruleset import body_of, wanted_body

# A heuristic: no real GitHub message for an organization policy is verified yet. A block counts
# only when the message names a policy of an organization or an enterprise. The words that name
# an access problem (SAML, OAuth apps, IP allow lists, tokens, roles, membership, scopes) keep a
# block as a failure, so a real access problem does not hide as a skip. A policy block that does
# not say "policy" fails, in plain sight.
POLICY = re.compile(r"\bpolic(y|ies)\b", re.IGNORECASE)
OWNER = re.compile(r"\b(organi[sz]ation|enterprise|org)\b", re.IGNORECASE)
EXCLUDED = re.compile(
    r"\b(saml|sso|single sign-on|oauth|ip allow|allow list|allowlist|tokens?|bypass|actors?"
    r"|owners?|admin\w*|permissions?|two-factor|2fa|credentials?|approv\w*|access\w*"
    r"|members?|membership|authori[sz]\w*|scopes?|managed users?)\b",
    re.IGNORECASE,
)


def is_policy_block(error: GhError) -> bool:
    return (
        error.status in {403, 422}
        and POLICY.search(error.detail) is not None
        and OWNER.search(error.detail) is not None
        and EXCLUDED.search(error.detail) is None
    )


def request_for(operation: Operation) -> tuple[str, str, dict[str, Any]]:
    """The method, the path under the repo, and the body of the request for an operation."""
    if isinstance(operation, UpdateRepository):
        return "PATCH", "", operation.changes.model_dump(exclude_none=True)
    if isinstance(operation, CreateLabel):
        return "POST", "/labels", operation.label.model_dump()
    if isinstance(operation, CreateRuleset):
        return "POST", "/rulesets", operation.ruleset.model_dump()
    if isinstance(operation, UpdateRuleset):
        return "PUT", f"/rulesets/{operation.id}", operation.ruleset.model_dump()
    raise ValueError(f"no request for the operation {operation.op}")


def failure(operation: Operation, error: GhError) -> Result:
    outcome = "skipped" if is_policy_block(error) else "failed"
    return Result(operation=operation, outcome=outcome, reason=error.detail)


CANNOT_CONFIRM = "cannot confirm that the ruleset is unchanged since it was read"


def changed_since_read(target: Path, repo: Repo, operation: UpdateRuleset) -> str | None:
    """Return why a PUT must not go out, or None.

    A PUT replaces the whole ruleset, so an edit between the read and the PUT would be lost. The
    command runs only after a live read, which has `updated_at`. A missing time fails closed.
    """
    if operation.expected_updated_at is None:
        return f"{CANNOT_CONFIRM}: the read had no updated_at"
    now = gh.api(target, repo, f"repos/{repo.slug}/rulesets/{operation.id}")
    stamp = now.get("updated_at") if isinstance(now, dict) else None
    if not isinstance(stamp, str):
        return f"{CANNOT_CONFIRM}: the answer has no updated_at"
    if stamp != operation.expected_updated_at:
        return "the ruleset changed after it was read; run again"
    return None


def check_response(
    operation: Operation, response: Any, desired: DesiredState, default_branch: str | None
) -> Result:
    """Compare what GitHub answered with what was asked, because GitHub can ignore a change."""
    if not isinstance(response, dict):
        raise ValueError("the answer is not an object")
    if isinstance(operation, UpdateRepository):
        asked = operation.changes.model_dump(exclude_none=True)
        ignored = sorted(name for name, value in asked.items() if response.get(name) != value)
        if not ignored:
            return Result(operation=operation, outcome="applied")
        reason = f"GitHub did not apply: {', '.join(ignored)}"
        # Some applied: the result is `applied`, with a reason. None applied: `skipped`.
        outcome = "skipped" if len(ignored) == len(asked) else "applied"
        return Result(operation=operation, outcome=outcome, reason=reason)
    if isinstance(operation, (CreateRuleset, UpdateRuleset)):
        ruleset = CurrentRuleset.model_validate(response)
        if wanted_body(desired, ruleset, default_branch) != body_of(ruleset):
            reason = "GitHub did not apply part of the ruleset"
            return Result(operation=operation, outcome="skipped", reason=reason)
    return Result(operation=operation, outcome="applied")


def run_push(target: Path, repo: Repo, operation: Push) -> Result:
    """Push the first commit. The report says which commit went to GitHub, even on a failure."""
    branch = operation.branch
    try:
        pushed = gitutil.push(target, branch)
        commit = gitutil.head_commit(target, branch)
    except BootstrapError as error:
        return Result(operation=operation, outcome="failed", reason=str(error))
    done = f"pushed {commit} to refs/heads/{branch}"
    if pushed.exit_code != 0:
        reason = f"{done}, but git exited {pushed.exit_code}"
        return Result(operation=operation, outcome="failed", reason=reason)
    if pushed.flag == "*":  # the push made the branch. Someone else could have pushed first.
        try:
            now = gh.api(target, repo, f"repos/{repo.slug}")
        except GhError as error:
            reason = f"{done}, but cannot read the default branch: {error.detail}"
            return Result(operation=operation, outcome="failed", reason=reason)
        default = now.get("default_branch") if isinstance(now, dict) else None
        if default != branch:
            reason = f"{done}, but GitHub's default branch is {default}"
            return Result(operation=operation, outcome="failed", reason=reason)
    return Result(operation=operation, outcome="applied", reason=done)


def run_operation(
    target: Path,
    repo: Repo,
    operation: Operation,
    desired: DesiredState,
    default_branch: str | None,
) -> Result:
    if isinstance(operation, Push):
        return run_push(target, repo, operation)
    method, suffix, body = request_for(operation)
    try:
        if isinstance(operation, UpdateRuleset):
            stale = changed_since_read(target, repo, operation)
            if stale:
                return Result(operation=operation, outcome="failed", reason=stale)
        response = gh.api(target, repo, f"repos/{repo.slug}{suffix}", method=method, body=body)
    except GhError as error:
        return failure(operation, error)
    try:
        return check_response(operation, response, desired, default_branch)
    except Exception as error:  # noqa: BLE001 - the write applied, so a failed check keeps the result
        reason = f"GitHub applied it, but the response could not be checked: {error}"
        return Result(operation=operation, outcome="applied", reason=reason)


def execute(
    target: Path,
    repo: Repo,
    plan: Plan,
    desired: DesiredState,
    default_branch: str | None,
) -> Report:
    """Run each operation in order. The first failure stops the run, and the report still prints."""
    results: list[Result] = []
    stopped = False
    for operation in plan.operations:
        if stopped:
            reason = "an earlier operation failed"
            results.append(Result(operation=operation, outcome="not_run", reason=reason))
            continue
        try:
            result = run_operation(target, repo, operation, desired, default_branch)
        except Exception as error:  # noqa: BLE001 - the report must always print
            result = Result(
                operation=operation, outcome="failed", reason=f"unexpected error: {error}"
            )
        results.append(result)
        stopped = result.outcome == "failed"
    return Report(results=results, plan_skipped=plan.skipped)

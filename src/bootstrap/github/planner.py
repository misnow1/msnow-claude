"""The planner: a pure function from desired and current state to a plan."""

from bootstrap.errors import BootstrapError
from bootstrap.github.models import (
    CreateLabel,
    CreateRuleset,
    CurrentState,
    DesiredState,
    Operation,
    Plan,
    Push,
    RepositoryChanges,
    Skip,
    UpdateRepository,
    UpdateRuleset,
)
from bootstrap.github.ruleset import body_of, wanted_body

# GitHub accepts only some title and message pairs, so the two always change together.
SQUASH_FIELDS = ("squash_merge_commit_title", "squash_merge_commit_message")
NOT_AVAILABLE = (
    "rulesets are not available for this repo: a private repo needs GitHub Pro, Team or Enterprise"
)
NEEDS_ADMIN = "needs admin access to read the ruleset"
NOT_A_BRANCH_RULESET = "a repo ruleset with this name exists but does not target branches"


def repository_changes(desired: DesiredState, current: CurrentState) -> RepositoryChanges | None:
    wanted = desired.repository.model_dump()
    actual = current.repository.model_dump()
    changed = {name for name, value in wanted.items() if actual[name] != value}
    if changed & set(SQUASH_FIELDS):
        changed |= set(SQUASH_FIELDS)
    if not changed:
        return None
    return RepositoryChanges(**{name: wanted[name] for name in changed})


def ruleset_plan(
    desired: DesiredState, current: CurrentState
) -> tuple[list[Operation], list[Skip]]:
    if not current.rulesets_available:
        return [], [Skip(operation="ruleset", reason=NOT_AVAILABLE)]
    if desired.ruleset.name in current.unreadable_rulesets:
        return [], [Skip(operation="ruleset", reason=NEEDS_ADMIN)]  # a create would fail with 422
    # An organization ruleset is never a match, so the command changes only repo settings.
    existing = next(
        (
            r
            for r in current.rulesets
            if r.source_type == "Repository" and r.name == desired.ruleset.name
        ),
        None,
    )
    default_branch = current.repository.default_branch
    if existing is None:
        return [CreateRuleset(ruleset=wanted_body(desired, None, default_branch))], []
    if existing.target != "branch":
        return [], [Skip(operation="ruleset", reason=NOT_A_BRANCH_RULESET)]
    body = wanted_body(desired, existing, default_branch)
    if body == body_of(existing):
        return [], []
    return [UpdateRuleset(id=existing.id, ruleset=body)], []


def plan(desired: DesiredState, current: CurrentState, branch: str | None = None) -> Plan:
    operations: list[Operation] = []
    if current.empty:
        if branch is None:
            raise BootstrapError("the repo is empty, and the plan needs its current branch.")
        operations.append(Push(branch=branch))
    changes = repository_changes(desired, current)
    if changes is not None:
        operations.append(UpdateRepository(changes=changes))
    existing = {label.name.lower() for label in current.labels}
    operations.extend(
        CreateLabel(label=label) for label in desired.labels if label.name.lower() not in existing
    )
    ruleset_operations, skipped = ruleset_plan(desired, current)
    return Plan(operations=[*operations, *ruleset_operations], skipped=skipped)

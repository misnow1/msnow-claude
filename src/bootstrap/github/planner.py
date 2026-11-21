"""The planner: a pure function from desired and current state to a plan."""

from bootstrap.github.models import (
    CreateLabel,
    CurrentState,
    DesiredState,
    Operation,
    Plan,
    RepositoryChanges,
    UpdateRepository,
)

# GitHub accepts only some title and message pairs, so the two always change together.
SQUASH_FIELDS = ("squash_merge_commit_title", "squash_merge_commit_message")


def repository_changes(desired: DesiredState, current: CurrentState) -> RepositoryChanges | None:
    wanted = desired.repository.model_dump()
    actual = current.repository.model_dump()
    changed = {name for name, value in wanted.items() if actual[name] != value}
    if changed & set(SQUASH_FIELDS):
        changed |= set(SQUASH_FIELDS)
    if not changed:
        return None
    return RepositoryChanges(**{name: wanted[name] for name in changed})


def plan(desired: DesiredState, current: CurrentState) -> Plan:
    operations: list[Operation] = []
    changes = repository_changes(desired, current)
    if changes is not None:
        operations.append(UpdateRepository(changes=changes))
    existing = {label.name.lower() for label in current.labels}
    operations.extend(
        CreateLabel(label=label) for label in desired.labels if label.name.lower() not in existing
    )
    return Plan(operations=operations)

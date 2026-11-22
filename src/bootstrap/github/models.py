"""Models for the GitHub settings: desired state, current state, operations and the plan."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

COLOR = r"^[0-9A-Fa-f]{6}$"


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Desired(Model):
    """The desired side is strict, so a string such as "yes" is not a boolean."""

    model_config = ConfigDict(strict=True)


class Current(Model):
    """The current side takes what GitHub reports and ignores the keys it does not use."""

    model_config = ConfigDict(extra="ignore")


# GitHub rejects the other pairs of squash title and message.
SQUASH_PAIRS = {
    "PR_TITLE": {"PR_BODY", "COMMIT_MESSAGES", "BLANK"},
    "COMMIT_OR_PR_TITLE": {"COMMIT_MESSAGES"},
}


class RepositorySettings(Desired):
    allow_squash_merge: bool
    allow_merge_commit: bool
    allow_rebase_merge: bool
    squash_merge_commit_title: Literal["PR_TITLE", "COMMIT_OR_PR_TITLE"]
    squash_merge_commit_message: Literal["PR_BODY", "COMMIT_MESSAGES", "BLANK"]
    delete_branch_on_merge: bool
    allow_auto_merge: bool

    @model_validator(mode="after")
    def squash_pair_is_allowed(self) -> "RepositorySettings":
        allowed = SQUASH_PAIRS[self.squash_merge_commit_title]
        if self.squash_merge_commit_message not in allowed:
            raise ValueError(
                f"squash_merge_commit_title {self.squash_merge_commit_title} needs "
                f"squash_merge_commit_message {' or '.join(sorted(allowed))}"
            )
        return self


class Label(Desired):
    name: str = Field(min_length=1)
    color: str = Field(pattern=COLOR)
    description: str


class RulesetSettings(Desired):
    name: str = Field(min_length=1)
    required_approving_review_count: int = Field(ge=0, le=10)
    dismiss_stale_reviews_on_push: bool
    allowed_merge_methods: list[Literal["merge", "squash", "rebase"]] = Field(min_length=1)

    @field_validator("allowed_merge_methods")
    @classmethod
    def methods_are_unique(cls, methods: list[str]) -> list[str]:
        if len(set(methods)) != len(methods):
            raise ValueError("duplicate merge methods")
        return methods


class DesiredState(Desired):
    repository: RepositorySettings
    labels: list[Label]
    ruleset: RulesetSettings
    required_checks: list[Annotated[str, Field(min_length=1)]]

    @field_validator("required_checks")
    @classmethod
    def check_names_are_unique(cls, names: list[str]) -> list[str]:
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"duplicate check names: {', '.join(duplicates)}")
        return names

    @field_validator("labels")
    @classmethod
    def label_names_are_unique(cls, labels: list[Label]) -> list[Label]:
        names = [label.name.lower() for label in labels]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"duplicate label names: {', '.join(duplicates)}")
        return labels


class CurrentRepository(Current):
    """What GitHub reports. Every value can be absent or unexpected."""

    allow_squash_merge: bool | None = None
    allow_merge_commit: bool | None = None
    allow_rebase_merge: bool | None = None
    squash_merge_commit_title: str | None = None
    squash_merge_commit_message: str | None = None
    delete_branch_on_merge: bool | None = None
    allow_auto_merge: bool | None = None
    default_branch: str | None = None


class CurrentLabel(Current):
    name: str
    color: str
    description: str | None = None


class CurrentRule(Current):
    type: str
    parameters: dict[str, Any] | None = None


class CurrentRuleset(Current):
    """A repo ruleset as the detail endpoint returns it.

    The list endpoint lacks `conditions`, `rules` and `bypass_actors`. They have no default, so
    a state that comes from the list fails to validate. A default would plan an update that
    removes the rules of the user.
    """

    id: int
    name: str
    source_type: str
    updated_at: str | None = None
    target: str | None = None
    enforcement: str | None = None
    conditions: dict[str, Any] | None
    rules: list[CurrentRule]
    bypass_actors: list[dict[str, Any]]


class CurrentState(Current):
    repository: CurrentRepository
    labels: list[CurrentLabel]
    empty: bool = False  # the repo has no branches
    rulesets_available: bool = True
    rulesets: list[CurrentRuleset] = []  # repo rulesets only, each from the detail endpoint
    unreadable_rulesets: list[str] = []  # names of repo rulesets that the caller cannot read


class RepositoryChanges(Model):
    """The repository fields to change. A field that stays as it is has no value."""

    allow_squash_merge: bool | None = None
    allow_merge_commit: bool | None = None
    allow_rebase_merge: bool | None = None
    squash_merge_commit_title: str | None = None
    squash_merge_commit_message: str | None = None
    delete_branch_on_merge: bool | None = None
    allow_auto_merge: bool | None = None


class UpdateRepository(Model):
    op: Literal["update_repository"] = "update_repository"
    changes: RepositoryChanges


class CreateLabel(Model):
    op: Literal["create_label"] = "create_label"
    label: Label


class Push(Model):
    op: Literal["push"] = "push"
    branch: str


class RulesetBody(Model):
    """The body of the GitHub request that creates or replaces a ruleset."""

    name: str
    target: str
    enforcement: str
    conditions: dict[str, Any]
    rules: list[dict[str, Any]]
    bypass_actors: list[dict[str, Any]]


class CreateRuleset(Model):
    op: Literal["create_ruleset"] = "create_ruleset"
    ruleset: RulesetBody


class UpdateRuleset(Model):
    op: Literal["update_ruleset"] = "update_ruleset"
    id: int
    ruleset: RulesetBody
    # `updated_at` of the ruleset when it was read. A PUT replaces the whole ruleset, so the run
    # checks that nobody changed the ruleset since. None makes the run fail closed. Only a dry-run
    # state file can lack it.
    expected_updated_at: str | None = None


Operation = Annotated[
    Push | UpdateRepository | CreateLabel | CreateRuleset | UpdateRuleset,
    Field(discriminator="op"),
]


class Skip(Model):
    operation: str
    reason: str


class Plan(Model):
    operations: list[Operation] = []
    skipped: list[Skip] = []


class Result(Model):
    """What happened to one operation.

    `skipped` is a policy block, or a change that GitHub ignored: a PATCH with no field applied,
    or a ruleset that GitHub cut down (the ruleset exists, but not as planned).
    """

    operation: Operation
    outcome: Literal["applied", "skipped", "failed", "not_run"]
    reason: str | None = None


class Report(Model):
    results: list[Result] = []
    plan_skipped: list[Skip] = []  # the skips of the plan, not the outcome `skipped`

    @property
    def failed(self) -> bool:
        return any(result.outcome == "failed" for result in self.results)

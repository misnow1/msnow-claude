"""Models for the GitHub settings: desired state, current state, operations and the plan."""

from typing import Annotated, Literal

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


class DesiredState(Desired):
    repository: RepositorySettings
    labels: list[Label]
    required_checks: list[str]

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


class CurrentLabel(Current):
    name: str
    color: str
    description: str | None = None


class CurrentState(Current):
    repository: CurrentRepository
    labels: list[CurrentLabel]


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


Operation = Annotated[UpdateRepository | CreateLabel, Field(discriminator="op")]


class Skip(Model):
    operation: str
    reason: str


class Plan(Model):
    operations: list[Operation] = []
    skipped: list[Skip] = []

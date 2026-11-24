"""The shared CI workflow and the required checks in the settings file of a generated repo."""

import json
from pathlib import Path

import pytest
import yaml
from conftest import FIXTURES, REPO_ROOT, FixtureTemplate, make_repo, run_github
from pipeline_support import LANGUAGE_SETS, Apply, applied_repo, workflow_problems

PIN = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
ONLY_PR = "on:\n  pull_request:\n"


def workflow(on: str = ONLY_PR, job: str = "", uses: str = PIN, name: str = "file-length") -> str:
    """The text of a workflow with one job. `job` holds extra keys of the job, indented by 4."""
    return (
        f"name: W\n{on}jobs:\n  {name}:\n    name: {name}\n    runs-on: ubuntu-latest\n{job}"
        f"    steps:\n      - uses: {uses}\n      - run: make length\n"
    )


def test_a_workflow_that_follows_every_rule_has_no_problem() -> None:
    assert workflow_problems({"a.yml": workflow()}, ["file-length"]) == []


@pytest.mark.parametrize(
    ("files", "required", "problem"),
    [
        (
            {"a.yml": workflow(), "b.yml": workflow()},
            ["file-length"],
            "job name used twice: file-length",
        ),
        ({"a.yml": workflow()}, ["other"], "required check without a job: other"),
        (
            {"a.yml": workflow(job="    strategy:\n      matrix:\n        os: [a, b]\n")},
            ["file-length"],
            "required job file-length uses a matrix",
        ),
        (
            {"a.yml": workflow(on="on:\n  pull_request:\n    paths: ['src/**']\n")},
            ["file-length"],
            "required job file-length: pull_request has a paths filter",
        ),
        (
            {"a.yml": workflow(on="on:\n  pull_request:\n    branches: [main]\n")},
            ["file-length"],
            "required job file-length: pull_request has a branches filter",
        ),
        (
            {"a.yml": workflow(on="on:\n  pull_request:\n    types: [opened, edited]\n")},
            ["file-length"],
            "required job file-length: pull_request types lack reopened, synchronize",
        ),
        (
            {"a.yml": workflow(on="on:\n  push:\n    branches: [main]\n")},
            ["file-length"],
            "required job file-length has no pull_request trigger",
        ),
        (
            {"a.yml": workflow(on="on: push\n")},
            ["file-length"],
            "required job file-length has no pull_request trigger",
        ),
        (
            {
                "a.yml": "on:\n  pull_request:\njobs:\n  file-length:\n"
                "    uses: org/repo/.github/workflows/w.yml@"
                "0123456789012345678901234567890123456789\n"
            },
            ["file-length"],
            "required job file-length uses a reusable workflow",
        ),
        (
            {"a.yml": workflow(uses="actions/checkout@v7")},
            ["file-length"],
            "action not pinned to a full SHA: actions/checkout@v7",
        ),
        (
            {"a.yml": workflow(uses="actions/checkout@3d3c42e")},
            ["file-length"],
            "action not pinned to a full SHA: actions/checkout@3d3c42e",
        ),
        ({"a.yml": workflow()}, [], "no required checks"),
        (
            {"a.yml": workflow(on="on:\n  pull_request:\n    paths-ignore: ['docs/**']\n")},
            ["file-length"],
            "required job file-length: pull_request has a paths-ignore filter",
        ),
        (
            {"a.yml": workflow(on="on:\n  pull_request:\n    branches-ignore: [dev]\n")},
            ["file-length"],
            "required job file-length: pull_request has a branches-ignore filter",
        ),
        (
            {"a.yml": workflow(on="on:\n  pull_request:\n    types: opened\n")},
            ["file-length"],
            "required job file-length: pull_request types lack reopened, synchronize",
        ),
        (
            {"a.yml": workflow(job="    if: github.event_name == 'push'\n")},
            ["file-length"],
            "required job file-length has an if condition",
        ),
        (
            {"a.yml": workflow(job="    continue-on-error: true\n")},
            ["file-length"],
            "required job file-length has continue-on-error",
        ),
        (
            {"a.yml": workflow(job="    strategy: ${{ fromJSON(vars.S) }}\n")},
            ["file-length"],
            "required job file-length uses a matrix",
        ),
    ],
    ids=[
        "a duplicate name",
        "a required name with no job",
        "a matrix",
        "a paths filter",
        "a branches filter",
        "types without synchronize",
        "no pull_request trigger",
        "a string trigger",
        "a reusable workflow",
        "an action with a tag",
        "an abbreviated SHA",
        "no required check",
        "a paths-ignore filter",
        "a branches-ignore filter",
        "types as one string",
        "an if condition",
        "continue-on-error",
        "a matrix as an expression",
    ],
)
def test_each_rule_reports_its_violation(
    files: dict[str, str], required: list[str], problem: str
) -> None:
    assert workflow_problems(files, required) == [problem]


@pytest.mark.parametrize(
    "on",
    [
        "on: pull_request\n",
        "on: [push, pull_request]\n",
        "on:\n  pull_request:\n    types: [opened, edited, synchronize, reopened]\n",
    ],
    ids=["a string", "a list", "types with all three needed"],
)
def test_other_forms_of_the_trigger_pass(on: str) -> None:
    assert workflow_problems({"a.yml": workflow(on=on)}, ["file-length"]) == []


def test_an_action_pinned_by_a_docker_digest_passes() -> None:
    digest = "docker://alpine@sha256:" + "a" * 64

    assert workflow_problems({"a.yml": workflow(uses=digest)}, ["file-length"]) == []


def test_a_job_that_is_not_required_may_use_a_matrix() -> None:
    matrix = workflow(job="    strategy:\n      matrix:\n        os: [a, b]\n", name="python-test")

    assert workflow_problems({"a.yml": workflow(), "b.yml": matrix}, ["file-length"]) == []


def test_an_unpinned_action_in_a_job_that_is_not_required_is_reported() -> None:
    loose = workflow(uses="actions/checkout@v7", name="python-test")

    problems = workflow_problems({"a.yml": workflow(), "b.yml": loose}, ["file-length"])

    assert problems == ["action not pinned to a full SHA: actions/checkout@v7"]


@pytest.mark.parametrize("name", LANGUAGE_SETS)
def test_the_generated_workflows_follow_the_rules_for_the_required_checks(
    tmp_path: Path, released: FixtureTemplate, run_apply: Apply, name: str
) -> None:
    target = applied_repo(tmp_path, released, run_apply, LANGUAGE_SETS[name])

    files = {
        path.name: path.read_text()
        for path in sorted((target / ".github" / "workflows").glob("*.y*ml"))
    }
    required = yaml.safe_load((target / ".github" / "bootstrap.yml").read_text())["required_checks"]

    assert "file-length" in required
    assert workflow_problems(files, required) == []


def test_the_file_length_job_calls_the_shared_make_target() -> None:
    text = (REPO_ROOT / "template" / ".github" / "workflows" / "shared.yml").read_text()

    steps = yaml.safe_load(text)["jobs"]["file-length"]["steps"]

    assert {"run": "make length"} in steps


def test_the_dry_run_adds_the_file_length_check_to_the_ruleset(
    tmp_path: Path, released: FixtureTemplate, run_apply: Apply
) -> None:
    target = make_repo(tmp_path / "repo")
    applied = run_apply(target, released.path)

    result = run_github(target, "--dry-run", "--state", str(FIXTURES / "matching.json"))

    assert applied.returncode == 0, applied.stderr
    assert result.returncode == 0, result.stderr
    [update] = json.loads(result.stdout)["operations"]
    [rule] = [r for r in update["ruleset"]["rules"] if r["type"] == "required_status_checks"]
    assert rule["parameters"]["required_status_checks"] == [
        {"context": "file-length", "integration_id": 15368}
    ]


ALL_SLOTS = ["python (#10)", "go (#11)", "rust (#12)", "docs (#14)", "pull request checks (#13)"]
SLOTS = {
    ".github/bootstrap.yml.jinja": ALL_SLOTS,
    ".pre-commit-config.yaml.jinja": ALL_SLOTS,
    ".github/workflows/shared.yml": ["docs (#14)", "pull request checks (#13)"],
}


@pytest.mark.parametrize("name", SLOTS)
def test_each_ticket_has_one_slot_line_in_a_shared_file(name: str) -> None:
    # The language tickets and #13 and #14 add their lines below their own slot, and keep the slot.
    # Git merges edits of two adjacent lines as one conflict, so each ticket needs its own line.
    text = (REPO_ROOT / "template" / name).read_text()

    for slot in SLOTS[name]:
        lines = [line for line in text.splitlines() if slot in line]
        assert len(lines) == 1, (name, slot, lines)

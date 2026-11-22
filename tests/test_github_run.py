"""The `github` subcommand that runs the plan through `gh`.

A stateful fake `gh` plays GitHub, so these tests never call the real service.
"""

import copy
import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from conftest import FIXTURES, FakeGh, add_origin, fake_state, git, repo_with_settings, run_github

from bootstrap.github.executor import execute
from bootstrap.github.gh import check_write
from bootstrap.github.models import CreateLabel, Plan
from bootstrap.github.repo import Repo
from bootstrap.github.settings_file import load_desired

Change = Callable[[dict[str, Any]], None]
EMPTY_REPORT: dict[str, list[Any]] = {"results": [], "plan_skipped": []}


def fixture(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / name).read_text())
    return data


def prepare(
    tmp_path: Path, fake_gh: FakeGh, name: str = "new-repo.json", change: Change | None = None
) -> Path:
    """A target repo, and a fake GitHub that holds a state fixture."""
    state = fake_state(fixture(name))
    if change:
        change(state)
    fake_gh.install(state)
    target = add_origin(repo_with_settings(tmp_path / "repo"))
    git(target, "symbolic-ref", "HEAD", "refs/heads/main")
    return target


def run(
    target: Path, fake_gh: FakeGh, *extra: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run the command against the fake GitHub, and check that no call left the repo."""
    result = run_github(target, *extra, env={**fake_gh.env, **(env or {})})
    fake_gh.assert_repo_scoped()
    return result


def test_a_new_repo_is_set_up_and_a_second_run_changes_nothing(
    tmp_path: Path, fake_gh: FakeGh
) -> None:
    target = prepare(tmp_path, fake_gh)

    first = run(target, fake_gh)

    assert first.returncode == 0, first.stderr
    report = json.loads(first.stdout)
    assert [r["operation"]["op"] for r in report["results"]] == [
        "update_repository",
        "create_label",
        "create_label",
        "create_label",
        "create_label",
        "create_ruleset",
    ]
    assert {r["outcome"] for r in report["results"]} == {"applied"}
    assert [(w["method"], w["args"][1]) for w in fake_gh.writes()] == [
        ("PATCH", "repos/octo/widgets"),
        *[("POST", "repos/octo/widgets/labels")] * 4,
        ("POST", "repos/octo/widgets/rulesets"),
    ]
    fake_gh.assert_repo_scoped()
    state = fake_gh.state()
    assert state["repo"]["allow_auto_merge"] is True
    assert {"needs-triage", "needs-info", "ready-for-agent", "ready-for-human"} <= {
        label["name"] for label in state["labels"]
    }
    assert [r["name"] for r in state["rulesets"]] == ["protect-default-branch"]

    fake_gh.clear_log()
    second = run(target, fake_gh)

    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout) == EMPTY_REPORT
    assert fake_gh.writes() == []


def results(proc: subprocess.CompletedProcess[str]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = json.loads(proc.stdout)["results"]
    return found


def outcomes(proc: subprocess.CompletedProcess[str]) -> list[str]:
    return [r["outcome"] for r in results(proc)]


def no_ruleset(state: dict[str, Any]) -> None:
    state["rulesets"] = []


def rule(body: dict[str, Any], kind: str) -> dict[str, Any]:
    found: dict[str, Any] = next(r for r in body["rules"] if r["type"] == kind)
    return found


def user_ruleset(state: dict[str, Any]) -> None:
    """A ruleset that a user changed: other rules and actors, a stricter count, no dismissal."""
    ruleset = state["rulesets"][0]
    ruleset["conditions"]["ref_name"]["include"].append("refs/heads/release")
    ruleset["rules"].insert(2, {"type": "required_signatures"})
    parameters = rule(ruleset, "pull_request")["parameters"]
    parameters.update(
        required_approving_review_count=2,
        dismiss_stale_reviews_on_push=False,
        require_code_owner_review=True,
    )
    ruleset["bypass_actors"].append({"actor_id": 9, "actor_type": "Team", "bypass_mode": "always"})


def test_a_user_edited_ruleset_is_put_with_the_edits_kept_and_then_converges(
    tmp_path: Path, fake_gh: FakeGh
) -> None:
    target = prepare(tmp_path, fake_gh, "matching.json", user_ruleset)

    first = run(target, fake_gh)

    assert first.returncode == 0, first.stderr
    assert outcomes(first) == ["applied"]
    [put] = fake_gh.writes()
    assert (put["method"], put["args"][1]) == ("PUT", "repos/octo/widgets/rulesets/27775135")
    assert rule(put["body"], "required_signatures") == {"type": "required_signatures"}
    parameters = rule(put["body"], "pull_request")["parameters"]
    assert parameters["dismiss_stale_reviews_on_push"] is True
    assert parameters["required_approving_review_count"] == 2
    assert parameters["require_code_owner_review"] is True
    assert {"actor_id": 9, "actor_type": "Team", "bypass_mode": "always"} in put["body"][
        "bypass_actors"
    ]
    fake_gh.assert_repo_scoped()

    fake_gh.clear_log()
    second = run(target, fake_gh)

    assert json.loads(second.stdout) == EMPTY_REPORT
    assert fake_gh.writes() == []


BLOCKS = {
    "policy 403": (
        403,
        "Repository rule creation is restricted by organization policy",
        None,
        "skipped",
    ),
    "policy 422 with errors": (
        422,
        "Validation Failed",
        ["blocked by enterprise policy"],
        "skipped",
    ),
    "policy word and associated": (
        422,
        "organization policy: not associated with this repository",
        None,
        "skipped",
    ),
    "admin rights": (403, "Must have admin rights to Repository.", None, "failed"),
    "saml": (
        403,
        "Resource protected by organization SAML enforcement. You must grant your token access.",
        None,
        "failed",
    ),
    "oauth app restriction": (
        403,
        "The organization has enabled OAuth App access restrictions",
        None,
        "failed",
    ),
    "ip allow list": (403, "The organization IP allow list denies this request", None, "failed"),
    "token policy": (
        403,
        "The organization forbids access via a fine-grained personal access token",
        None,
        "failed",
    ),
    "plural tokens": (
        403,
        "Fine-grained personal access tokens are not allowed by this organization policy",
        None,
        "failed",
    ),
    "plural actors": (
        403,
        "actors of type Integration are not allowed by policy",
        None,
        "failed",
    ),
    "owners only": (403, "Only organization owners can create repository rulesets", None, "failed"),
    "needs an admin": (403, "You must be an organization admin", None, "failed"),
    "two factor": (403, "This organization requires two-factor authentication", None, "failed"),
    "not approved": (
        403,
        "the octo organization has not approved your access",
        None,
        "failed",
    ),
    "policy without an organization": (403, "This policy is not supported", None, "failed"),
    "short org name": (403, "Org policy: signed commits are required", None, "skipped"),
    "not a member": (403, "Your account is not a member of this organization", None, "failed"),
    "not authorized": (403, "User is not authorized by the organization", None, "failed"),
    "insufficient scopes": (403, "Forbidden by organization: insufficient scopes", None, "failed"),
    "managed users": (403, "Enterprise Managed Users cannot create labels here", None, "failed"),
    "not accessible": (
        403,
        "organization policy: the repository is not accessible",
        None,
        "failed",
    ),
    "name must be unique": (422, "Validation Failed", ["Name must be unique"], "failed"),
    "bad bypass actor": (
        422,
        "Validation Failed",
        ["organization bypass actor is invalid"],
        "failed",
    ),
}


@pytest.mark.parametrize(("status", "message", "errors", "outcome"), BLOCKS.values(), ids=BLOCKS)
def test_a_block_on_the_ruleset_is_skipped_only_for_an_organization_policy(
    tmp_path: Path,
    fake_gh: FakeGh,
    status: int,
    message: str,
    errors: list[str] | None,
    outcome: str,
) -> None:
    def change(state: dict[str, Any]) -> None:
        state["errors"] = {
            "POST repos/octo/widgets/rulesets": {
                "status": status,
                "message": message,
                "errors": errors,
            }
        }

    target = prepare(tmp_path, fake_gh, change=change)

    result = run(target, fake_gh)

    assert outcomes(result) == ["applied"] * 5 + [outcome]
    assert result.returncode == (1 if outcome == "failed" else 0)
    assert message in results(result)[-1]["reason"]
    fake_gh.assert_repo_scoped()


def test_a_failure_stops_the_run_and_a_rerun_finishes_it(tmp_path: Path, fake_gh: FakeGh) -> None:
    def change(state: dict[str, Any]) -> None:
        state["errors"] = {
            "POST repos/octo/widgets/labels": {
                "status": 422,
                "message": "Validation Failed",
                "after": 1,
            }
        }

    target = prepare(tmp_path, fake_gh, change=change)

    first = run(target, fake_gh)

    assert first.returncode == 1
    assert outcomes(first) == ["applied", "applied", "failed", "not_run", "not_run", "not_run"]
    assert "an earlier operation failed" in results(first)[-1]["reason"]

    def heal(state: dict[str, Any]) -> None:
        del state["errors"]

    fake_gh.update(heal)
    second = run(target, fake_gh)

    assert second.returncode == 0, second.stderr
    assert outcomes(second) == ["applied"] * 4
    third = run(target, fake_gh)
    assert json.loads(third.stdout) == EMPTY_REPORT


def only_auto_merge_differs(state: dict[str, Any]) -> None:
    state["repo"]["allow_auto_merge"] = False
    state["errors"] = {}


def test_a_setting_that_github_ignores_is_skipped_and_reported_every_run(
    tmp_path: Path, fake_gh: FakeGh
) -> None:
    def change(state: dict[str, Any]) -> None:
        only_auto_merge_differs(state)
        state["ignore_fields"] = ["allow_auto_merge"]

    target = prepare(tmp_path, fake_gh, "matching.json", change)

    for _ in range(2):  # the plan cannot know the limit, so the same PATCH comes back
        result = run(target, fake_gh)
        assert result.returncode == 0, result.stderr
        assert outcomes(result) == ["skipped"]
        assert results(result)[0]["reason"] == "GitHub did not apply: allow_auto_merge"


def test_a_patch_that_applies_only_some_fields_is_applied_with_a_reason(
    tmp_path: Path, fake_gh: FakeGh
) -> None:
    def change(state: dict[str, Any]) -> None:
        state["ignore_fields"] = ["allow_auto_merge"]

    target = prepare(tmp_path, fake_gh, change=change)

    result = run(target, fake_gh)

    assert result.returncode == 0, result.stderr
    assert results(result)[0]["outcome"] == "applied"
    assert results(result)[0]["reason"] == "GitHub did not apply: allow_auto_merge"


def test_a_ruleset_that_github_cut_down_is_skipped(tmp_path: Path, fake_gh: FakeGh) -> None:
    def change(state: dict[str, Any]) -> None:
        no_ruleset(state)
        state["drop_on_write"] = "deletion"

    target = prepare(tmp_path, fake_gh, "matching.json", change)

    result = run(target, fake_gh)

    assert result.returncode == 0, result.stderr
    assert outcomes(result) == ["skipped"]
    assert "did not apply part of the ruleset" in results(result)[0]["reason"]


def test_a_response_that_cannot_be_checked_keeps_the_result_as_applied(
    tmp_path: Path, fake_gh: FakeGh
) -> None:
    def change(state: dict[str, Any]) -> None:
        no_ruleset(state)
        state["response_without"] = ["bypass_actors"]

    target = prepare(tmp_path, fake_gh, "matching.json", change)

    result = run(target, fake_gh)

    assert result.returncode == 0, result.stderr
    assert outcomes(result) == ["applied"]
    assert "could not be checked" in results(result)[0]["reason"]


def test_a_ruleset_that_changed_after_it_was_read_is_not_overwritten(
    tmp_path: Path, fake_gh: FakeGh
) -> None:
    def change(state: dict[str, Any]) -> None:
        user_ruleset(state)
        state["touch_after_first_detail_read"] = True

    target = prepare(tmp_path, fake_gh, "matching.json", change)

    result = run(target, fake_gh)

    assert result.returncode == 1
    assert outcomes(result) == ["failed"]
    assert "changed after it was read" in results(result)[0]["reason"]
    assert fake_gh.writes() == []


def with_slow_write(state: dict[str, Any]) -> None:
    no_ruleset(state)
    state["sleep"] = 3


def with_gateway_error(state: dict[str, Any]) -> None:
    no_ruleset(state)
    state["errors"] = {"POST repos/octo/widgets/labels": {"status": 502, "message": "Bad Gateway"}}


def with_garbage_answer(state: dict[str, Any]) -> None:
    no_ruleset(state)
    state["garbage_on_write"] = True


@pytest.mark.parametrize(
    "change",
    [with_slow_write, with_gateway_error, with_garbage_answer],
    ids=["timeout", "5xx", "garbage"],
)
def test_a_write_whose_result_is_unknown_fails_and_the_report_still_prints(
    tmp_path: Path, fake_gh: FakeGh, change: Change
) -> None:
    def state_change(state: dict[str, Any]) -> None:
        change(state)
        state["labels"] = [lb for lb in state["labels"] if lb["name"] != "needs-triage"]

    target = prepare(tmp_path, fake_gh, "matching.json", state_change)

    result = run(target, fake_gh, env={"BOOTSTRAP_GH_TIMEOUT": "1"})

    assert result.returncode == 1
    assert outcomes(result) == ["failed", "not_run"]
    assert "the result is unknown" in results(result)[0]["reason"]


@pytest.mark.parametrize("value", ["abc", "0", "-5", "1.5", "", "\u00b2", "99999"])
def test_a_bad_timeout_stops_the_command_before_any_gh_call(
    tmp_path: Path, fake_gh: FakeGh, value: str
) -> None:
    target = prepare(tmp_path, fake_gh)

    result = run(target, fake_gh, env={"BOOTSTRAP_GH_TIMEOUT": value})

    assert result.returncode == 1
    assert "BOOTSTRAP_GH_TIMEOUT" in result.stderr
    assert fake_gh.calls() == []


def test_an_organization_ruleset_with_the_same_name_is_never_changed(
    tmp_path: Path, fake_gh: FakeGh
) -> None:
    def change(state: dict[str, Any]) -> None:
        org = copy.deepcopy(state["rulesets"][0])
        org.update(id=99, source_type="Organization")
        state["org_rulesets"] = [org]
        state["rulesets"] = []

    target = prepare(tmp_path, fake_gh, "matching.json", change)

    result = run(target, fake_gh)

    assert result.returncode == 0, result.stderr
    assert [(w["method"], w["args"][1]) for w in fake_gh.writes()] == [
        ("POST", "repos/octo/widgets/rulesets")
    ]
    fake_gh.assert_repo_scoped()


def test_a_renamed_repo_stops_the_command_before_any_write(tmp_path: Path, fake_gh: FakeGh) -> None:
    def change(state: dict[str, Any]) -> None:
        state["repo"]["full_name"] = "octo/renamed"

    target = prepare(tmp_path, fake_gh, change=change)

    result = run(target, fake_gh)

    assert result.returncode == 1
    assert result.stdout == ""
    assert "octo/renamed" in result.stderr
    assert fake_gh.writes() == []


def test_a_state_file_cannot_be_used_to_change_github(tmp_path: Path, fake_gh: FakeGh) -> None:
    target = prepare(tmp_path, fake_gh)

    result = run(target, fake_gh, "--state", str(FIXTURES / "new-repo.json"))

    assert result.returncode == 1
    assert "only works with --dry-run" in result.stderr
    assert fake_gh.calls() == []


def test_a_live_dry_run_writes_nothing(tmp_path: Path, fake_gh: FakeGh) -> None:
    target = prepare(tmp_path, fake_gh)

    result = run(target, fake_gh, "--dry-run")

    assert result.returncode == 0, result.stderr
    assert "operations" in json.loads(result.stdout)
    assert fake_gh.writes() == []


@pytest.mark.parametrize(
    "answer", [None, {"id": 27775135}, [1], "x"], ids=["null", "no timestamp", "list", "string"]
)
def test_a_ruleset_that_cannot_be_read_again_is_not_overwritten(
    tmp_path: Path, fake_gh: FakeGh, answer: Any
) -> None:
    def change(state: dict[str, Any]) -> None:
        user_ruleset(state)
        state["reread_override"] = {"repos/octo/widgets/rulesets/27775135": answer}

    target = prepare(tmp_path, fake_gh, "matching.json", change)

    result = run(target, fake_gh)

    assert result.returncode == 1
    assert outcomes(result) == ["failed"]
    assert "cannot confirm" in results(result)[0]["reason"]
    assert fake_gh.writes() == []


def test_a_live_read_without_a_timestamp_does_not_put(tmp_path: Path, fake_gh: FakeGh) -> None:
    def change(state: dict[str, Any]) -> None:
        user_ruleset(state)
        del state["rulesets"][0]["updated_at"]

    target = prepare(tmp_path, fake_gh, "matching.json", change)

    result = run(target, fake_gh)

    assert result.returncode == 1
    assert "cannot confirm" in results(result)[0]["reason"]
    assert fake_gh.writes() == []


def test_a_write_with_an_empty_answer_has_an_unknown_result(
    tmp_path: Path, fake_gh: FakeGh
) -> None:
    def change(state: dict[str, Any]) -> None:
        state["empty_on_write"] = True

    target = prepare(tmp_path, fake_gh, change=change)

    result = run(target, fake_gh)

    assert result.returncode == 1
    assert outcomes(result)[0] == "failed"
    assert "the result is unknown" in results(result)[0]["reason"]
    assert set(outcomes(result)[1:]) == {"not_run"}


def test_a_patch_answer_that_cannot_be_checked_keeps_the_result_as_applied(
    tmp_path: Path, fake_gh: FakeGh
) -> None:
    def change(state: dict[str, Any]) -> None:
        state["response_as"] = [1]

    target = prepare(tmp_path, fake_gh, change=change)

    result = run(target, fake_gh)

    assert results(result)[0]["outcome"] == "applied"
    assert "could not be checked: the answer is not an object" in results(result)[0]["reason"]


REFUSED = [
    ("PATCH", "repos/octo/widgets/../../orgs/x"),
    ("PATCH", "repos/octo/widgets?x=1"),
    ("POST", "repos/octo/widgets//labels"),
    ("POST", "repos/octo/widgets-other/labels"),
    ("POST", "orgs/octo/rulesets"),
    ("DELETE", "repos/octo/widgets/labels/bug"),
    ("GET", "repos/octo/widgets"),
]


@pytest.mark.parametrize(("method", "endpoint"), REFUSED)
def test_the_write_guard_refuses_anything_but_a_write_to_the_repo(
    method: str, endpoint: str
) -> None:
    with pytest.raises(ValueError, match="refusing"):
        check_write(Repo("octo", "widgets"), endpoint, method)


def test_the_write_guard_accepts_a_write_under_the_repo() -> None:
    check_write(Repo("octo", "widgets"), "repos/octo/widgets/labels", "POST")
    check_write(Repo("octo", "widgets"), "repos/octo/widgets", "PATCH")


def test_an_unexpected_error_in_one_operation_still_gives_a_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    desired = load_desired(repo_with_settings(tmp_path / "repo"))
    label = CreateLabel(label=desired.labels[0])

    def explode(operation: object) -> None:
        raise RuntimeError("something odd")

    monkeypatch.setattr("bootstrap.github.executor.request_for", explode)

    report = execute(
        tmp_path, Repo("octo", "widgets"), Plan(operations=[label, label]), desired, "main"
    )

    assert [r.outcome for r in report.results] == ["failed", "not_run"]
    assert "something odd" in str(report.results[0].reason)
    assert report.failed

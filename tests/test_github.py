"""The `github` subcommand, through the CLI. Tests never call GitHub."""

import copy
import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from conftest import (
    FIXTURES,
    SETTINGS_TEMPLATE,
    FixtureTemplate,
    git,
    make_repo,
    repo_with_settings,
    run_github,
)

Apply = Callable[..., subprocess.CompletedProcess[str]]
VALID = SETTINGS_TEMPLATE.read_text()
MATCHING = json.loads((FIXTURES / "matching.json").read_text())


RULESET = {
    "name": "protect-default-branch",
    "target": "branch",
    "enforcement": "active",
    "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
    "rules": [
        {"type": "deletion"},
        {"type": "non_fast_forward"},
        {
            "type": "pull_request",
            "parameters": {
                "required_approving_review_count": 0,
                "dismiss_stale_reviews_on_push": True,
                "require_code_owner_review": False,
                "require_last_push_approval": False,
                "required_review_thread_resolution": False,
                "allowed_merge_methods": ["squash"],
            },
        },
    ],
    "bypass_actors": [
        {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "pull_request"}
    ],
}


def state_with(tmp_path: Path, base: str = "matching.json", **changes: object) -> Path:
    """Write a state file: a fixture with the given top-level keys replaced."""
    state = json.loads((FIXTURES / base).read_text())
    return write_state(tmp_path, {**state, **changes})


def write_state(tmp_path: Path, state: dict[str, object]) -> Path:
    path = tmp_path / "state.json"
    path.write_text(json.dumps(state))
    return path


def test_matching_state_gives_an_empty_plan(tmp_path: Path) -> None:
    target = repo_with_settings(tmp_path / "repo")

    result = run_github(target, "--dry-run", "--state", str(FIXTURES / "matching.json"))

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"operations": [], "skipped": []}


def test_new_repo_gets_the_repository_update_and_the_missing_labels(tmp_path: Path) -> None:
    target = repo_with_settings(tmp_path / "repo")

    result = run_github(target, "--dry-run", "--state", str(FIXTURES / "new-repo.json"))

    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    assert plan["skipped"] == []
    update, *labels, ruleset = plan["operations"]
    assert update == {
        "op": "update_repository",
        "changes": {
            "allow_merge_commit": False,
            "allow_rebase_merge": False,
            "squash_merge_commit_title": "PR_TITLE",
            "squash_merge_commit_message": "PR_BODY",
            "delete_branch_on_merge": True,
            "allow_auto_merge": True,
        },
    }
    assert [(op["op"], op["label"]["name"]) for op in labels] == [
        ("create_label", name)
        for name in ["needs-triage", "needs-info", "ready-for-agent", "ready-for-human"]
    ]
    assert ruleset == {"op": "create_ruleset", "ruleset": RULESET}


def test_label_in_another_case_gets_no_operation(tmp_path: Path) -> None:
    target = repo_with_settings(tmp_path / "repo")
    labels = [
        {**label, "name": label["name"].upper(), "color": "123456"} for label in MATCHING["labels"]
    ]
    state = write_state(tmp_path, {**MATCHING, "labels": labels})

    result = run_github(target, "--dry-run", "--state", str(state))

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["operations"] == []


def test_a_changed_squash_title_sends_both_squash_fields(tmp_path: Path) -> None:
    target = repo_with_settings(tmp_path / "repo")
    repository = {**MATCHING["repository"], "squash_merge_commit_title": "COMMIT_OR_PR_TITLE"}
    state = write_state(tmp_path, {**MATCHING, "repository": repository})

    result = run_github(target, "--dry-run", "--state", str(state))

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["operations"] == [
        {
            "op": "update_repository",
            "changes": {
                "squash_merge_commit_title": "PR_TITLE",
                "squash_merge_commit_message": "PR_BODY",
            },
        }
    ]


BAD_FILES = {
    "negative review count": (
        VALID.replace("required_approving_review_count: 0", "required_approving_review_count: -1"),
        "required_approving_review_count",
    ),
    "unknown merge method": (
        VALID.replace("allowed_merge_methods: [squash]", "allowed_merge_methods: [fast-forward]"),
        "allowed_merge_methods",
    ),
    "empty check name": (
        VALID.replace("required_checks: []", 'required_checks: [""]'),
        "required_checks",
    ),
    "duplicate checks": (
        VALID.replace("required_checks: []", "required_checks: [lint, lint]"),
        "duplicate check names",
    ),
    "empty": ("", "empty"),
    "not yaml": ("repository: [", "not valid YAML"),
    "unknown key": (VALID + "surprise: 1\n", "surprise"),
    "wrong type": (
        VALID.replace("allow_auto_merge: true", "allow_auto_merge: maybe"),
        "allow_auto_merge",
    ),
    "truthy string": (
        VALID.replace("allow_auto_merge: true", 'allow_auto_merge: "yes"'),
        "allow_auto_merge",
    ),
    "number as bool": (
        VALID.replace("allow_merge_commit: false", "allow_merge_commit: 0"),
        "allow_merge_commit",
    ),
    "duplicate key": (
        VALID.replace(
            "allow_auto_merge: true", "allow_auto_merge: true\n  allow_auto_merge: false"
        ),
        "duplicate key",
    ),
    "squash pair GitHub rejects": (
        VALID.replace(
            "squash_merge_commit_title: PR_TITLE", "squash_merge_commit_title: COMMIT_OR_PR_TITLE"
        ),
        "squash",
    ),
    "unhashable key": (VALID + "? [a]\n: 1\n", "not valid YAML"),
    "duplicate labels": (
        VALID.replace("name: needs-info", "name: Needs-Triage"),
        "duplicate label names",
    ),
}


@pytest.mark.parametrize(("text", "message"), BAD_FILES.values(), ids=BAD_FILES.keys())
def test_an_invalid_settings_file_stops_the_command(
    tmp_path: Path, text: str, message: str
) -> None:
    target = repo_with_settings(tmp_path / "repo", text)

    result = run_github(target, "--dry-run", "--state", str(FIXTURES / "matching.json"))

    assert result.returncode == 1
    assert result.stdout == ""
    assert ".github/bootstrap.yml" in result.stderr
    assert message in result.stderr


def test_a_missing_settings_file_stops_the_command(tmp_path: Path) -> None:
    target = make_repo(tmp_path / "repo")

    result = run_github(target, "--dry-run", "--state", str(FIXTURES / "matching.json"))

    assert result.returncode == 1
    assert result.stdout == ""
    assert ".github/bootstrap.yml" in result.stderr


@pytest.mark.parametrize("content", ["not json", '{"repository": {}}'], ids=["json", "shape"])
def test_an_invalid_state_file_stops_the_command(tmp_path: Path, content: str) -> None:
    target = repo_with_settings(tmp_path / "repo")
    state = tmp_path / "state.json"
    state.write_text(content)

    result = run_github(target, "--dry-run", "--state", str(state))

    assert result.returncode == 1
    assert result.stdout == ""
    assert "state file" in result.stderr


def test_apply_writes_a_settings_file_that_github_accepts(
    tmp_path: Path, released: FixtureTemplate, run_apply: Apply
) -> None:
    target = make_repo(tmp_path / "repo")

    applied = run_apply(target, released.path)
    result = run_github(target, "--dry-run", "--state", str(FIXTURES / "matching.json"))

    assert applied.returncode == 0, applied.stderr
    assert yaml.safe_load((target / ".github/bootstrap.yml").read_text())["required_checks"] == []
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"operations": [], "skipped": []}


def test_a_changed_squash_message_sends_both_squash_fields(tmp_path: Path) -> None:
    target = repo_with_settings(tmp_path / "repo")
    repository = {**MATCHING["repository"], "squash_merge_commit_message": "BLANK"}
    state = write_state(tmp_path, {**MATCHING, "repository": repository})

    result = run_github(target, "--dry-run", "--state", str(state))

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["operations"] == [
        {
            "op": "update_repository",
            "changes": {
                "squash_merge_commit_title": "PR_TITLE",
                "squash_merge_commit_message": "PR_BODY",
            },
        }
    ]


def test_state_with_missing_fields_and_extra_api_keys_is_accepted(tmp_path: Path) -> None:
    target = repo_with_settings(tmp_path / "repo")
    state = write_state(
        tmp_path,
        {
            "repository": {"id": 1, "full_name": "o/r", "allow_auto_merge": None},
            "labels": [
                {"id": 9, "node_id": "x", "name": "bug", "color": "d73a4a", "default": True}
            ],
        },
    )

    result = run_github(target, "--dry-run", "--state", str(state))

    assert result.returncode == 0, result.stderr
    operations = json.loads(result.stdout)["operations"]
    assert operations[0]["changes"]["allow_auto_merge"] is True
    assert len(operations) == 7


def test_a_symlinked_settings_file_stops_the_command(tmp_path: Path) -> None:
    target = repo_with_settings(tmp_path / "repo")
    secret = tmp_path / "secret.txt"
    secret.write_text("TOPSECRET: [\n")
    (target / ".github/bootstrap.yml").unlink()
    (target / ".github/bootstrap.yml").symlink_to(secret)

    result = run_github(target, "--dry-run", "--state", str(FIXTURES / "matching.json"))

    assert result.returncode == 1
    assert "symlink" in result.stderr
    assert "TOPSECRET" not in result.stderr + result.stdout


def test_a_symlinked_github_directory_stops_the_command(tmp_path: Path) -> None:
    target = repo_with_settings(tmp_path / "repo")
    outside = tmp_path / "outside"
    (target / ".github").rename(outside)
    (target / ".github").symlink_to(outside)

    result = run_github(target, "--dry-run", "--state", str(FIXTURES / "matching.json"))

    assert result.returncode == 1
    assert "symlink" in result.stderr


def test_a_target_that_is_not_a_directory_stops_the_command(tmp_path: Path) -> None:
    result = run_github(
        tmp_path / "missing", "--dry-run", "--state", str(FIXTURES / "matching.json")
    )

    assert result.returncode == 1
    assert "not an existing directory" in result.stderr


@pytest.mark.parametrize("which", ["settings", "state"])
def test_a_file_that_is_not_utf8_names_the_file(tmp_path: Path, which: str) -> None:
    target = repo_with_settings(tmp_path / "repo")
    state = FIXTURES / "matching.json"
    if which == "settings":
        (target / ".github/bootstrap.yml").write_bytes(b"\xff\xfe")
    else:
        state = tmp_path / "state.json"
        state.write_bytes(b"\xff\xfe")

    result = run_github(target, "--dry-run", "--state", str(state))

    assert result.returncode == 1
    assert result.stdout == ""
    assert ("bootstrap.yml" if which == "settings" else "state.json") in result.stderr


def test_a_yaml_merge_key_is_accepted(tmp_path: Path) -> None:
    text = VALID.replace(
        "repository:\n  allow_squash_merge: true\n",
        "repository:\n  <<: {allow_squash_merge: true}\n",
    )
    assert text != VALID
    target = repo_with_settings(tmp_path / "repo", text)

    result = run_github(target, "--dry-run", "--state", str(FIXTURES / "matching.json"))

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"operations": [], "skipped": []}


def test_empty_repo_pushes_the_first_commit_before_the_ruleset(tmp_path: Path) -> None:
    target = repo_with_settings(tmp_path / "repo")
    git(target, "symbolic-ref", "HEAD", "refs/heads/main")

    result = run_github(
        target, "--dry-run", "--state", str(state_with(tmp_path, "new-repo.json", empty=True))
    )

    assert result.returncode == 0, result.stderr
    operations = json.loads(result.stdout)["operations"]
    assert operations[0] == {"op": "push", "branch": "main"}
    assert operations[-1] == {"op": "create_ruleset", "ruleset": RULESET}


def test_private_repo_on_free_skips_the_ruleset(tmp_path: Path) -> None:
    target = repo_with_settings(tmp_path / "repo")

    result = run_github(
        target,
        "--dry-run",
        "--state",
        str(state_with(tmp_path, rulesets_available=False, rulesets=[])),
    )

    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    assert plan["operations"] == []
    assert [skip["operation"] for skip in plan["skipped"]] == ["ruleset"]
    assert "not available" in plan["skipped"][0]["reason"]


def test_an_empty_private_repo_on_free_still_pushes(tmp_path: Path) -> None:
    target = repo_with_settings(tmp_path / "repo")
    git(target, "symbolic-ref", "HEAD", "refs/heads/main")

    result = run_github(
        target,
        "--dry-run",
        "--state",
        str(state_with(tmp_path, empty=True, rulesets_available=False, rulesets=[])),
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["operations"] == [{"op": "push", "branch": "main"}]


Change = Callable[[dict[str, Any]], None]


def ruleset_plan(
    tmp_path: Path,
    change: Change | None = None,
    checks: str = "[]",
    edits: tuple[tuple[str, str], ...] = (),
) -> dict[str, Any]:
    """Plan against the matching state, after `change` edits its ruleset and `edits` the file."""
    state = copy.deepcopy(MATCHING)
    if change:
        change(state["rulesets"][0])
    text = VALID.replace("required_checks: []", f"required_checks: {checks}")
    for old, new in edits:
        assert old in text
        text = text.replace(old, new)
    target = repo_with_settings(tmp_path / "repo", text)
    result = run_github(target, "--dry-run", "--state", str(write_state(tmp_path, state)))
    assert result.returncode == 0, result.stderr
    plan: dict[str, Any] = json.loads(result.stdout)
    return plan


def rule(body: dict[str, Any], kind: str) -> dict[str, Any]:
    found: dict[str, Any] = next(r for r in body["rules"] if r["type"] == kind)
    return found


def test_the_body_of_a_create_plan_is_a_state_with_nothing_left_to_plan(tmp_path: Path) -> None:
    def adopt(ruleset: dict[str, Any]) -> None:
        # What GitHub returns for the ruleset that the create body made.
        ruleset.clear()
        ruleset.update(copy.deepcopy(RULESET), id=7, source_type="Repository", node_id="n")
        parameters = rule(ruleset, "pull_request")["parameters"]
        parameters.update(
            required_reviewers=[], require_extra_approval_for_unattributed_changes=True
        )

    assert ruleset_plan(tmp_path, adopt) == {"operations": [], "skipped": []}


def user_ruleset(ruleset: dict[str, Any]) -> None:
    ruleset["conditions"]["ref_name"]["include"].append("refs/heads/release")
    ruleset["rules"].insert(2, {"type": "required_signatures"})
    parameters = rule(ruleset, "pull_request")["parameters"]
    parameters.update(
        required_approving_review_count=2,
        dismiss_stale_reviews_on_push=False,
        require_code_owner_review=True,
    )
    ruleset["bypass_actors"].append({"actor_id": 9, "actor_type": "Team", "bypass_mode": "always"})


def test_an_update_sets_what_bootstrap_owns_and_keeps_what_the_user_added(tmp_path: Path) -> None:
    plan = ruleset_plan(tmp_path, user_ruleset)

    [update] = plan["operations"]
    body = update["ruleset"]
    assert update["op"] == "update_ruleset"
    assert update["id"] == MATCHING["rulesets"][0]["id"]
    parameters = rule(body, "pull_request")["parameters"]
    assert parameters["dismiss_stale_reviews_on_push"] is True
    assert parameters["required_approving_review_count"] == 2
    assert parameters["require_code_owner_review"] is True
    assert rule(body, "required_signatures") == {"type": "required_signatures"}
    assert "refs/heads/release" in body["conditions"]["ref_name"]["include"]
    assert {"actor_id": 9, "actor_type": "Team", "bypass_mode": "always"} in body["bypass_actors"]


def test_the_body_of_an_update_plan_is_a_state_with_nothing_left_to_plan(tmp_path: Path) -> None:
    [update] = ruleset_plan(tmp_path, user_ruleset)["operations"]

    def adopt(ruleset: dict[str, Any]) -> None:
        ruleset.update(update["ruleset"])

    (tmp_path / "again").mkdir()
    assert ruleset_plan(tmp_path / "again", adopt) == {"operations": [], "skipped": []}


def set_enforcement(ruleset: dict[str, Any]) -> None:
    ruleset["enforcement"] = "evaluate"


def set_admin_mode(ruleset: dict[str, Any]) -> None:
    ruleset["bypass_actors"][0]["bypass_mode"] = "always"


def exclude_default(ruleset: dict[str, Any]) -> None:
    ruleset["conditions"]["ref_name"]["exclude"] = ["~DEFAULT_BRANCH"]


def exclude_by_pattern(ruleset: dict[str, Any]) -> None:
    ruleset["conditions"]["ref_name"]["exclude"] = ["refs/heads/*"]


def exclude_by_negated_class(ruleset: dict[str, Any]) -> None:
    ruleset["conditions"]["ref_name"]["exclude"] = ["refs/heads/ma[^x]n"]


def exclude_by_braces(ruleset: dict[str, Any]) -> None:
    ruleset["conditions"]["ref_name"]["exclude"] = ["refs/heads/{main,dev}"]


def drop_conditions(ruleset: dict[str, Any]) -> None:
    ruleset["conditions"] = None


def drop_deletion_rule(ruleset: dict[str, Any]) -> None:
    ruleset["rules"] = [r for r in ruleset["rules"] if r["type"] != "deletion"]


def drop_pull_request_rule(ruleset: dict[str, Any]) -> None:
    ruleset["rules"] = [r for r in ruleset["rules"] if r["type"] != "pull_request"]


def drop_bypass_actors(ruleset: dict[str, Any]) -> None:
    ruleset["bypass_actors"] = []


@pytest.mark.parametrize(
    "change",
    [
        set_enforcement,
        set_admin_mode,
        exclude_default,
        exclude_by_pattern,
        exclude_by_negated_class,
        exclude_by_braces,
        drop_conditions,
        drop_deletion_rule,
        drop_pull_request_rule,
        drop_bypass_actors,
    ],
)
def test_a_ruleset_that_misses_an_owned_value_is_repaired_to_the_standard(
    tmp_path: Path, change: Change
) -> None:
    [update] = ruleset_plan(tmp_path, change)["operations"]

    body = update["ruleset"]
    assert update["op"] == "update_ruleset"
    assert {key: body[key] for key in ("enforcement", "conditions", "bypass_actors")} == {
        key: RULESET[key] for key in ("enforcement", "conditions", "bypass_actors")
    }
    assert {"type": "deletion"} in body["rules"]
    assert {"type": "non_fast_forward"} in body["rules"]
    owned = rule(RULESET, "pull_request")["parameters"]
    parameters = rule(body, "pull_request")["parameters"]
    assert {key: parameters[key] for key in owned} == owned

    def adopt(ruleset: dict[str, Any]) -> None:
        ruleset.update(body)

    (tmp_path / "again").mkdir()
    assert ruleset_plan(tmp_path / "again", adopt) == {"operations": [], "skipped": []}


def test_an_exclude_that_does_not_hit_the_default_branch_stays(tmp_path: Path) -> None:
    def change(ruleset: dict[str, Any]) -> None:
        ruleset["conditions"]["ref_name"]["exclude"] = ["~DEFAULT_BRANCH", "refs/heads/dev"]

    [update] = ruleset_plan(tmp_path, change)["operations"]

    assert update["ruleset"]["conditions"]["ref_name"]["exclude"] == ["refs/heads/dev"]


def test_a_higher_count_in_the_file_raises_the_count(tmp_path: Path) -> None:
    edits = (("required_approving_review_count: 0", "required_approving_review_count: 1"),)

    [update] = ruleset_plan(tmp_path, edits=edits)["operations"]

    assert (
        rule(update["ruleset"], "pull_request")["parameters"]["required_approving_review_count"]
        == 1
    )


def test_the_same_merge_methods_in_another_order_need_no_update(tmp_path: Path) -> None:
    def change(ruleset: dict[str, Any]) -> None:
        rule(ruleset, "pull_request")["parameters"]["allowed_merge_methods"] = ["squash", "rebase"]

    edits = (("allowed_merge_methods: [squash]", "allowed_merge_methods: [rebase, squash]"),)

    assert ruleset_plan(tmp_path, change, edits=edits) == {"operations": [], "skipped": []}


def test_a_new_pull_request_rule_has_the_parameters_that_github_requires(tmp_path: Path) -> None:
    [update] = ruleset_plan(tmp_path, drop_pull_request_rule)["operations"]

    assert rule(update["ruleset"], "pull_request") == rule(RULESET, "pull_request")


def test_a_check_pinned_to_another_app_is_pinned_to_github_actions(tmp_path: Path) -> None:
    def change(ruleset: dict[str, Any]) -> None:
        ruleset["rules"].append(
            {
                "type": "required_status_checks",
                "parameters": {
                    "required_status_checks": [{"context": "python-lint", "integration_id": 999}],
                    "strict_required_status_checks_policy": False,
                },
            }
        )

    [update] = ruleset_plan(tmp_path, change, checks="[python-lint]")["operations"]

    checks = rule(update["ruleset"], "required_status_checks")["parameters"][
        "required_status_checks"
    ]
    assert checks == [{"context": "python-lint", "integration_id": 15368}]


def test_a_null_review_count_counts_as_zero(tmp_path: Path) -> None:
    def change(ruleset: dict[str, Any]) -> None:
        rule(ruleset, "pull_request")["parameters"]["required_approving_review_count"] = None

    [update] = ruleset_plan(tmp_path, change)["operations"]

    assert (
        rule(update["ruleset"], "pull_request")["parameters"]["required_approving_review_count"]
        == 0
    )


def test_a_ruleset_that_does_not_target_branches_is_skipped_before_any_merge(
    tmp_path: Path,
) -> None:
    def change(ruleset: dict[str, Any]) -> None:
        ruleset["target"] = "tag"
        rule(ruleset, "pull_request")["parameters"]["allowed_merge_methods"] = None

    plan = ruleset_plan(tmp_path, change)

    assert plan["operations"] == []
    assert "does not target branches" in plan["skipped"][0]["reason"]


@pytest.mark.parametrize("value", [None, "missing"], ids=["null", "missing"])
def test_a_state_ruleset_needs_a_source_type(tmp_path: Path, value: str | None) -> None:
    target = repo_with_settings(tmp_path / "repo")
    state = copy.deepcopy(MATCHING)
    if value == "missing":
        del state["rulesets"][0]["source_type"]
    else:
        state["rulesets"][0]["source_type"] = value

    result = run_github(target, "--dry-run", "--state", str(write_state(tmp_path, state)))

    assert result.returncode == 1
    assert "source_type" in result.stderr


def test_an_organization_ruleset_with_the_same_name_is_not_touched(tmp_path: Path) -> None:
    def make_org(ruleset: dict[str, Any]) -> None:
        ruleset["source_type"] = "Organization"

    plan = ruleset_plan(tmp_path, make_org)

    assert [op["op"] for op in plan["operations"]] == ["create_ruleset"]


def test_a_repo_ruleset_with_the_name_that_does_not_target_branches_is_skipped(
    tmp_path: Path,
) -> None:
    def make_tag(ruleset: dict[str, Any]) -> None:
        ruleset["target"] = "tag"

    plan = ruleset_plan(tmp_path, make_tag)

    assert plan["operations"] == []
    assert "does not target branches" in plan["skipped"][0]["reason"]


def test_required_checks_are_pinned_to_github_actions(tmp_path: Path) -> None:
    [update] = ruleset_plan(tmp_path, checks="[python-lint]")["operations"]

    checks = rule(update["ruleset"], "required_status_checks")["parameters"]
    assert checks == {
        "required_status_checks": [{"context": "python-lint", "integration_id": 15368}],
        "strict_required_status_checks_policy": False,
    }


def test_an_unpinned_check_gets_pinned_and_other_checks_stay(tmp_path: Path) -> None:
    def add_checks(ruleset: dict[str, Any]) -> None:
        ruleset["rules"].append(
            {
                "type": "required_status_checks",
                "parameters": {
                    "required_status_checks": [{"context": "python-lint"}, {"context": "other"}],
                    "strict_required_status_checks_policy": True,
                },
            }
        )

    [update] = ruleset_plan(tmp_path, add_checks, checks="[python-lint]")["operations"]

    parameters = rule(update["ruleset"], "required_status_checks")["parameters"]
    assert parameters["required_status_checks"] == [
        {"context": "python-lint", "integration_id": 15368},
        {"context": "other"},
    ]
    assert parameters["strict_required_status_checks_policy"] is True


def test_a_state_ruleset_from_the_list_endpoint_is_not_accepted(tmp_path: Path) -> None:
    target = repo_with_settings(tmp_path / "repo")
    state = copy.deepcopy(MATCHING)
    del state["rulesets"][0]["rules"]

    result = run_github(target, "--dry-run", "--state", str(write_state(tmp_path, state)))

    assert result.returncode == 1
    assert result.stdout == ""
    assert "rules" in result.stderr


def test_an_empty_repo_that_is_not_a_git_repo_stops_the_command(tmp_path: Path) -> None:
    target = tmp_path / "repo"
    target.mkdir()
    (target / ".github").mkdir()
    (target / ".github/bootstrap.yml").write_text(VALID)

    result = run_github(
        target, "--dry-run", "--state", str(state_with(tmp_path, "new-repo.json", empty=True))
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert "current branch" in result.stderr


def plan_without_default_branch(
    tmp_path: Path, exclude: list[str]
) -> subprocess.CompletedProcess[str]:
    target = repo_with_settings(tmp_path / "repo")
    state = copy.deepcopy(MATCHING)
    del state["repository"]["default_branch"]
    state["rulesets"][0]["conditions"]["ref_name"]["exclude"] = exclude

    return run_github(target, "--dry-run", "--state", str(write_state(tmp_path, state)))


def test_an_exclude_pattern_needs_the_default_branch_in_the_state(tmp_path: Path) -> None:
    result = plan_without_default_branch(tmp_path, ["refs/heads/main"])

    assert result.returncode == 1
    assert result.stdout == ""
    assert "default_branch" in result.stderr


def test_a_whole_repo_exclude_needs_no_default_branch_in_the_state(tmp_path: Path) -> None:
    result = plan_without_default_branch(tmp_path, ["~DEFAULT_BRANCH"])

    assert result.returncode == 0, result.stderr
    [update] = json.loads(result.stdout)["operations"]
    assert update["ruleset"]["conditions"]["ref_name"]["exclude"] == []


def test_an_exclude_pattern_with_too_many_brace_options_stops_the_command(tmp_path: Path) -> None:
    pattern = "refs/heads/" + "{a,b}" * 14 + "x"

    target = repo_with_settings(tmp_path / "repo")
    state = copy.deepcopy(MATCHING)
    state["rulesets"][0]["conditions"]["ref_name"]["exclude"] = [pattern]

    result = run_github(target, "--dry-run", "--state", str(write_state(tmp_path, state)))

    assert result.returncode == 1
    assert result.stdout == ""
    assert "too many" in result.stderr

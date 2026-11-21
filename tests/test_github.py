"""The `github` subcommand, through the CLI. Tests never call GitHub."""

import json
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml
from conftest import (
    FIXTURES,
    SETTINGS_TEMPLATE,
    FixtureTemplate,
    make_repo,
    repo_with_settings,
    run_github,
)

Apply = Callable[..., subprocess.CompletedProcess[str]]
VALID = SETTINGS_TEMPLATE.read_text()
MATCHING = json.loads((FIXTURES / "matching.json").read_text())


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
    update, *labels = plan["operations"]
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
    assert len(operations) == 6


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

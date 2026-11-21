"""The `github` subcommand without a state file: it reads GitHub through `gh`.

A fake `gh` plays GitHub, so these tests never call the real service.
"""

import copy
import json
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from conftest import FIXTURES, FakeGh, add_origin, fake_state, git, repo_with_settings, run_github

Change = Callable[[dict[str, Any]], None]


def in_repo(endpoint: str) -> bool:
    """True for the repo itself or an endpoint under it, and not for `widgets-other`."""
    path = endpoint.partition("?")[0]
    return path == "repos/octo/widgets" or path.startswith("repos/octo/widgets/")


def fixture(name: str) -> dict[str, object]:
    data: dict[str, object] = json.loads((FIXTURES / name).read_text())
    return data


def test_the_live_plan_equals_the_plan_from_a_state_file_with_the_same_data(
    tmp_path: Path, fake_gh: FakeGh
) -> None:
    target = add_origin(repo_with_settings(tmp_path / "repo"))
    fake_gh.install(fake_state(fixture("new-repo.json")))
    from_file = run_github(target, "--dry-run", "--state", str(FIXTURES / "new-repo.json"))

    live = run_github(target, "--dry-run", env=fake_gh.env)

    assert live.returncode == 0, live.stderr
    assert live.stdout == from_file.stdout
    assert "GitHub repo octo/widgets" in live.stderr
    assert fake_gh.calls(), "the live read must call gh"
    assert all(call["args"][0] == "api" for call in fake_gh.calls())
    assert all(in_repo(call["args"][1]) for call in fake_gh.calls())


def live(
    tmp_path: Path, fake_gh: FakeGh, name: str = "matching.json", change: Change | None = None
) -> subprocess.CompletedProcess[str]:
    """Run the live dry-run against a fake GitHub that holds a state fixture."""
    state = fake_state(fixture(name))
    if change:
        change(state)
    fake_gh.install(state)
    target = add_origin(repo_with_settings(tmp_path / "repo"))
    git(target, "symbolic-ref", "HEAD", "refs/heads/main")
    return run_github(target, "--dry-run", env=fake_gh.env)


def test_a_matching_live_state_gives_the_empty_plan(tmp_path: Path, fake_gh: FakeGh) -> None:
    result = live(tmp_path, fake_gh)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"operations": [], "skipped": []}


def test_a_private_repo_on_free_skips_the_ruleset(tmp_path: Path, fake_gh: FakeGh) -> None:
    def change(state: dict[str, Any]) -> None:
        state["errors"] = {
            "repos/octo/widgets/rulesets": {
                "status": 403,
                "message": "Upgrade to GitHub Pro or make this repository public to enable it.",
            }
        }

    result = live(tmp_path, fake_gh, change=change)

    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    assert plan["operations"] == []
    assert [skip["operation"] for skip in plan["skipped"]] == ["ruleset"]
    assert "not available" in plan["skipped"][0]["reason"]


def test_a_ruleset_that_the_caller_cannot_read_is_skipped_and_not_created(
    tmp_path: Path, fake_gh: FakeGh
) -> None:
    def change(state: dict[str, Any]) -> None:
        state["hide_bypass"] = True

    result = live(tmp_path, fake_gh, change=change)

    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    assert plan["operations"] == []
    assert "needs admin access" in plan["skipped"][0]["reason"]


def test_more_than_100_labels_do_not_plan_a_duplicate(tmp_path: Path, fake_gh: FakeGh) -> None:
    def change(state: dict[str, Any]) -> None:
        extra = [{"name": f"label-{i}", "color": "ffffff", "description": None} for i in range(150)]
        state["labels"] = extra + state["labels"]

    result = live(tmp_path, fake_gh, change=change)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["operations"] == []


def test_an_organization_ruleset_is_never_read_or_planned(tmp_path: Path, fake_gh: FakeGh) -> None:
    def change(state: dict[str, Any]) -> None:
        org = copy.deepcopy(state["rulesets"][0])
        org.update(id=99, source_type="Organization")
        state["org_rulesets"] = [org]
        state["rulesets"] = []

    result = live(tmp_path, fake_gh, change=change)

    assert result.returncode == 0, result.stderr
    assert [op["op"] for op in json.loads(result.stdout)["operations"]] == ["create_ruleset"]
    endpoints = [call["args"][1] for call in fake_gh.calls()]
    assert "repos/octo/widgets/rulesets?includes_parents=false" in endpoints
    assert not any(endpoint.endswith("/rulesets/99") for endpoint in endpoints)


def test_an_empty_live_repo_pushes_the_local_default_branch(
    tmp_path: Path, fake_gh: FakeGh
) -> None:
    def change(state: dict[str, Any]) -> None:
        state["branches"] = []

    result = live(tmp_path, fake_gh, change=change)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["operations"][0] == {"op": "push", "branch": "main"}


def test_a_409_on_the_branches_call_means_an_empty_repo(tmp_path: Path, fake_gh: FakeGh) -> None:
    def change(state: dict[str, Any]) -> None:
        state["branches_409"] = True

    result = live(tmp_path, fake_gh, change=change)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["operations"][0] == {"op": "push", "branch": "main"}


def test_a_local_branch_other_than_the_default_stops_an_empty_repo_plan(
    tmp_path: Path, fake_gh: FakeGh
) -> None:
    def change(state: dict[str, Any]) -> None:
        state["branches"] = []

    state = fake_state(fixture("matching.json"))
    change(state)
    fake_gh.install(state)
    target = add_origin(repo_with_settings(tmp_path / "repo"))
    git(target, "symbolic-ref", "HEAD", "refs/heads/trunk")

    result = run_github(target, "--dry-run", env=fake_gh.env)

    assert result.returncode == 1
    assert result.stdout == ""
    assert "git branch -m main" in result.stderr


def test_a_state_file_of_an_empty_repo_needs_the_default_branch(tmp_path: Path) -> None:
    state = fixture("new-repo.json")
    repository = dict(state["repository"])  # type: ignore[call-overload]
    del repository["default_branch"]
    path = tmp_path / "state.json"
    path.write_text(json.dumps({**state, "repository": repository, "empty": True}))
    target = repo_with_settings(tmp_path / "repo")
    git(target, "symbolic-ref", "HEAD", "refs/heads/main")

    result = run_github(target, "--dry-run", "--state", str(path))

    assert result.returncode == 1
    assert "default_branch" in result.stderr


def test_the_state_file_path_makes_no_gh_call(tmp_path: Path, fake_gh: FakeGh) -> None:
    target = repo_with_settings(tmp_path / "repo")

    result = run_github(
        target, "--dry-run", "--state", str(FIXTURES / "matching.json"), env=fake_gh.env
    )

    assert result.returncode == 0, result.stderr
    assert fake_gh.calls() == []


def failing(tmp_path: Path, fake_gh: FakeGh, change: Change) -> subprocess.CompletedProcess[str]:
    result = live(tmp_path, fake_gh, change=change)
    assert result.returncode == 1
    assert result.stdout == ""
    return result


def test_without_admin_rights_the_command_stops(tmp_path: Path, fake_gh: FakeGh) -> None:
    def change(state: dict[str, Any]) -> None:
        state["repo"]["permissions"]["admin"] = False

    assert "admin rights on octo/widgets" in failing(tmp_path, fake_gh, change).stderr


def test_a_response_without_permissions_is_not_taken_for_admin(
    tmp_path: Path, fake_gh: FakeGh
) -> None:
    def change(state: dict[str, Any]) -> None:
        del state["repo"]["permissions"]

    assert "cannot confirm admin rights" in failing(tmp_path, fake_gh, change).stderr


def test_a_repo_that_github_does_not_know_stops_the_command(
    tmp_path: Path, fake_gh: FakeGh
) -> None:
    def change(state: dict[str, Any]) -> None:
        state["slug"] = "someone/else"

    assert "no repo octo/widgets" in failing(tmp_path, fake_gh, change).stderr


def test_an_old_gh_stops_the_command(tmp_path: Path, fake_gh: FakeGh) -> None:
    def change(state: dict[str, Any]) -> None:
        state["old_gh"] = True

    assert "gh 2.48 or later" in failing(tmp_path, fake_gh, change).stderr


def test_output_that_is_not_json_stops_the_command(tmp_path: Path, fake_gh: FakeGh) -> None:
    def change(state: dict[str, Any]) -> None:
        state["garbage"] = True

    assert "did not return JSON" in failing(tmp_path, fake_gh, change).stderr


def test_another_409_on_the_branches_call_stops_the_command(
    tmp_path: Path, fake_gh: FakeGh
) -> None:
    def change(state: dict[str, Any]) -> None:
        state["errors"] = {
            "repos/octo/widgets/branches": {"status": 409, "message": "Conflict somewhere else"}
        }

    assert "Conflict somewhere else" in failing(tmp_path, fake_gh, change).stderr


def test_a_missing_gh_stops_the_command(tmp_path: Path) -> None:
    target = add_origin(repo_with_settings(tmp_path / "repo"))
    only_git = tmp_path / "only-git"
    only_git.mkdir()
    (only_git / "git").symlink_to(shutil.which("git") or "git")

    result = run_github(target, "--dry-run", env={"PATH": str(only_git)})

    assert result.returncode == 1
    assert "gh is not installed" in result.stderr


def test_gh_gets_the_repo_and_host_and_no_debug_output(tmp_path: Path, fake_gh: FakeGh) -> None:
    state = fake_state(fixture("matching.json"))
    fake_gh.install(state)
    target = add_origin(repo_with_settings(tmp_path / "repo"))

    result = run_github(target, "--dry-run", env={**fake_gh.env, "GH_DEBUG": "api", "DEBUG": "1"})

    assert result.returncode == 0, result.stderr
    for call in fake_gh.calls():
        assert (call["GH_REPO"], call["GH_HOST"]) == ("octo/widgets", "github.com")
        assert (call["GH_DEBUG"], call["DEBUG"]) == (None, None)


GOOD_ORIGINS = [
    "https://github.com/octo/widgets.git",
    "https://github.com/octo/widgets",
    "git@github.com:octo/widgets.git",
    "ssh://git@github.com/octo/widgets.git",
    "https://x-access-token:SECRET@github.com/octo/widgets.git",
]


@pytest.mark.parametrize("origin", GOOD_ORIGINS)
def test_each_origin_form_names_the_same_repo_and_leaks_no_token(
    tmp_path: Path, fake_gh: FakeGh, origin: str
) -> None:
    fake_gh.install(fake_state(fixture("matching.json")))
    target = add_origin(repo_with_settings(tmp_path / "repo"), origin)

    result = run_github(target, "--dry-run", env=fake_gh.env)

    assert result.returncode == 0, result.stderr
    assert "GitHub repo octo/widgets" in result.stderr
    assert all(in_repo(c["args"][1]) for c in fake_gh.calls())
    log = fake_gh.log_path.read_text()
    assert "SECRET" not in result.stdout + result.stderr + log


def test_a_fork_clone_with_an_upstream_remote_and_a_stray_gh_repo_uses_origin(
    tmp_path: Path, fake_gh: FakeGh
) -> None:
    fake_gh.install(fake_state(fixture("matching.json")))
    target = add_origin(repo_with_settings(tmp_path / "repo"))
    git(target, "remote", "add", "upstream", "https://github.com/cli/cli.git")

    result = run_github(target, "--dry-run", env={**fake_gh.env, "GH_REPO": "cli/cli"})

    assert result.returncode == 0, result.stderr
    assert {c["GH_REPO"] for c in fake_gh.calls()} == {"octo/widgets"}
    assert all(in_repo(c["args"][1]) for c in fake_gh.calls())


def with_config(target: Path) -> None:
    git(target, "config", "remote.origin.pushurl", "https://github.com/other/thing.git")


def with_push_rewrite(target: Path) -> None:
    git(target, "config", "url.https://github.com/other/.pushInsteadOf", "https://github.com/octo/")


def with_alias_rewrite(target: Path) -> None:
    git(target, "remote", "set-url", "origin", "git@github.com:octo/widgets.git")
    git(target, "config", "url.git@github-work:.insteadOf", "git@github.com:")


def with_second_url(target: Path) -> None:
    git(target, "config", "--add", "remote.origin.url", "https://github.com/other/thing.git")


def with_second_pushurl(target: Path) -> None:
    git(target, "config", "--add", "remote.origin.pushurl", "https://github.com/octo/widgets.git")
    git(target, "config", "--add", "remote.origin.pushurl", "https://github.com/other/thing.git")


BAD_ORIGINS = {
    "other host": ("https://gitlab.com/octo/widgets.git", None, "not on github.com"),
    "upper case scp host that is not github": ("git@GitLab.com:o/r.git", None, "not on github.com"),
    "ssh alias": ("git@github-work:octo/widgets.git", None, "~/.ssh/config"),
    "dot name": ("https://github.com/octo/..", None, "OWNER/REPO"),
    "single dot name": ("https://github.com/octo/.", None, "OWNER/REPO"),
    "http": ("http://github.com/octo/widgets.git", None, "https or ssh"),
    "git protocol": ("git://github.com/octo/widgets.git", None, "https or ssh"),
    "file URL on a github.com host": ("file://github.com/tmp/x", None, "https or ssh"),
    "secret in the user part": (
        "https://SECRET/x@github.com/octo/widgets",
        None,
        "not on github.com",
    ),
    "two fetch URLs": (None, with_second_url, "more than one URL"),
    "two push URLs": (None, with_second_pushurl, "more than one URL"),
    "odd name": ("https://github.com/octo/wid%20gets", None, "OWNER/REPO"),
    "three parts": ("https://github.com/octo/widgets/extra", None, "OWNER/REPO"),
    "token and other host": (
        "https://x-access-token:SECRET@gitlab.com/o/r.git",
        None,
        "not on github.com",
    ),
    "pushurl names another repo": (None, with_config, "different repos"),
    "pushInsteadOf names another repo": (None, with_push_rewrite, "different repos"),
    "insteadOf rewrites to an alias": (None, with_alias_rewrite, "~/.ssh/config"),
}


@pytest.mark.parametrize(("origin", "setup", "message"), BAD_ORIGINS.values(), ids=BAD_ORIGINS)
def test_a_bad_origin_stops_the_command_before_any_gh_call(
    tmp_path: Path,
    fake_gh: FakeGh,
    origin: str | None,
    setup: Callable[[Path], None] | None,
    message: str,
) -> None:
    fake_gh.install(fake_state(fixture("matching.json")))
    target = add_origin(
        repo_with_settings(tmp_path / "repo"), origin or "https://github.com/octo/widgets.git"
    )
    if setup:
        setup(target)

    result = run_github(target, "--dry-run", env=fake_gh.env)

    assert result.returncode == 1
    assert result.stdout == ""
    assert message in result.stderr
    assert "SECRET" not in result.stderr
    assert fake_gh.calls() == []


def test_a_repo_without_an_origin_stops_the_command(tmp_path: Path, fake_gh: FakeGh) -> None:
    fake_gh.install(fake_state(fixture("matching.json")))
    target = repo_with_settings(tmp_path / "repo")

    result = run_github(target, "--dry-run", env=fake_gh.env)

    assert result.returncode == 1
    assert "origin" in result.stderr
    assert fake_gh.calls() == []


def test_an_upper_case_github_host_is_accepted_in_both_url_forms(
    tmp_path: Path, fake_gh: FakeGh
) -> None:
    fake_gh.install(fake_state(fixture("matching.json")))
    target = add_origin(repo_with_settings(tmp_path / "repo"), "git@GitHub.com:octo/widgets.git")

    result = run_github(target, "--dry-run", env=fake_gh.env)

    assert result.returncode == 0, result.stderr


def test_a_directory_that_is_not_a_git_repo_is_not_reported_as_a_missing_origin(
    tmp_path: Path, fake_gh: FakeGh
) -> None:
    target = tmp_path / "plain"
    (target / ".github").mkdir(parents=True)
    (target / ".github/bootstrap.yml").write_text(
        (FIXTURES / ".." / ".." / ".." / "template" / ".github" / "bootstrap.yml").read_text()
    )

    result = run_github(target, "--dry-run", env=fake_gh.env)

    assert result.returncode == 1
    assert "no origin remote" not in result.stderr
    assert "git repo" in result.stderr


def test_gh_loses_every_variable_that_makes_it_print_secrets_or_colors(
    tmp_path: Path, fake_gh: FakeGh
) -> None:
    fake_gh.install(fake_state(fixture("matching.json")))
    target = add_origin(repo_with_settings(tmp_path / "repo"))
    noisy = {"GODEBUG": "http2debug=2", "GH_FORCE_TTY": "1", "CLICOLOR_FORCE": "1"}

    result = run_github(target, "--dry-run", env={**fake_gh.env, **noisy})

    assert result.returncode == 0, result.stderr
    for call in fake_gh.calls():
        assert (call["GODEBUG"], call["GH_FORCE_TTY"], call["CLICOLOR_FORCE"]) == (None, None, None)


def test_a_failure_with_no_gh_line_shows_only_the_last_line_of_stderr(
    tmp_path: Path, fake_gh: FakeGh
) -> None:
    def change(state: dict[str, Any]) -> None:
        state["plain_error"] = True

    result = failing(tmp_path, fake_gh, change)

    assert "the real failure" in result.stderr
    assert "SECRET-HEADER" not in result.stderr


def test_a_test_that_forgets_the_fake_never_reaches_a_real_gh(tmp_path: Path) -> None:
    target = add_origin(repo_with_settings(tmp_path / "repo"))

    result = run_github(target, "--dry-run")

    assert result.returncode == 1
    assert "stub gh" in result.stderr


def test_an_organization_entry_in_the_list_is_filtered_even_if_github_leaks_it(
    tmp_path: Path, fake_gh: FakeGh
) -> None:
    def change(state: dict[str, Any]) -> None:
        org = copy.deepcopy(state["rulesets"][0])
        org.update(id=99, source_type="Organization")
        state["org_rulesets"] = [org]
        state["leak_org"] = True

    result = live(tmp_path, fake_gh, change=change)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["operations"] == []
    assert not any(c["args"][1].endswith("/rulesets/99") for c in fake_gh.calls())


def test_a_403_that_is_not_the_upgrade_message_does_not_hide_the_rulesets(
    tmp_path: Path, fake_gh: FakeGh
) -> None:
    def change(state: dict[str, Any]) -> None:
        state["errors"] = {
            "repos/octo/widgets/rulesets": {"status": 403, "message": "Resource not accessible"}
        }

    assert "Resource not accessible" in failing(tmp_path, fake_gh, change).stderr


def test_an_error_on_a_ruleset_detail_stops_the_command(tmp_path: Path, fake_gh: FakeGh) -> None:
    def change(state: dict[str, Any]) -> None:
        state["errors"] = {
            "repos/octo/widgets/rulesets/27775135": {"status": 404, "message": "Not Found"}
        }

    assert "Not Found" in failing(tmp_path, fake_gh, change).stderr


BAD_SHAPES = {
    "null repo": ("repos/octo/widgets", None),
    "repo that is a list": ("repos/octo/widgets", [1]),
    "rulesets that are no list": ("repos/octo/widgets/rulesets", {"x": 1}),
    "ruleset entry without an id": (
        "repos/octo/widgets/rulesets",
        [[{"source_type": "Repository", "name": "x"}]],
    ),
    "detail that is a list": ("repos/octo/widgets/rulesets/27775135", [1]),
    "detail with bypass_actors and no rules": (
        "repos/octo/widgets/rulesets/27775135",
        {"id": 27775135, "name": "x", "source_type": "Repository", "bypass_actors": []},
    ),
}


@pytest.mark.parametrize(("path", "answer"), BAD_SHAPES.values(), ids=BAD_SHAPES)
def test_an_answer_of_the_wrong_shape_is_an_error_and_not_a_traceback(
    tmp_path: Path, fake_gh: FakeGh, path: str, answer: object
) -> None:
    def change(state: dict[str, Any]) -> None:
        state["override"] = {path: answer}

    result = failing(tmp_path, fake_gh, change)

    assert "Traceback" not in result.stderr
    assert "bootstrap: error" in result.stderr


def test_a_string_for_the_admin_flag_is_not_admin(tmp_path: Path, fake_gh: FakeGh) -> None:
    def change(state: dict[str, Any]) -> None:
        state["repo"]["permissions"]["admin"] = "false"

    assert "admin rights" in failing(tmp_path, fake_gh, change).stderr


def test_a_directory_inside_another_repo_does_not_read_the_outer_repo(
    tmp_path: Path, fake_gh: FakeGh
) -> None:
    fake_gh.install(fake_state(fixture("matching.json")))
    outer = add_origin(repo_with_settings(tmp_path / "outer"))
    inner = outer / "sub"
    (inner / ".github").mkdir(parents=True)
    (inner / ".github/bootstrap.yml").write_text((outer / ".github/bootstrap.yml").read_text())

    result = run_github(inner, "--dry-run", env=fake_gh.env)

    assert result.returncode == 1
    assert result.stdout == ""
    assert "not the root" in result.stderr
    assert fake_gh.calls() == []


def test_a_missing_origin_is_found_in_any_language(tmp_path: Path, fake_gh: FakeGh) -> None:
    target = repo_with_settings(tmp_path / "repo")

    result = run_github(target, "--dry-run", env={**fake_gh.env, "LC_ALL": "de_DE.UTF-8"})

    assert result.returncode == 1
    assert "no origin remote" in result.stderr

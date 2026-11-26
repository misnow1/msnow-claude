"""`apply` on an empty repo: the first commit, and the GitHub step that follows it.

A fake `gh` plays GitHub and a fake `git` sends the push to a local bare repo, so these tests
never reach the real service.
"""

import json
import os
import signal
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml
from conftest import (
    BOOTSTRAP,
    FIXTURES,
    GIT_ENV,
    FakeGh,
    FixtureTemplate,
    add_origin,
    fake_state,
    git,
    make_repo,
    run_github,
)

Apply = Callable[..., subprocess.CompletedProcess[str]]
COMMIT_SUBJECT = "chore: apply the development standards"


def prepare(tmp_path: Path, fake_gh: FakeGh, name: str = "repo", origin: bool = True) -> Path:
    """A repo with no commit on `main`, and an empty fake GitHub with a bare origin."""
    state = fake_state(json.loads((FIXTURES / "new-repo.json").read_text()))
    fake_gh.install(state)
    fake_gh.bare()
    target = make_repo(tmp_path / name)
    git(target, "symbolic-ref", "HEAD", "refs/heads/main")
    return add_origin(target) if origin else target


def tree(target: Path) -> set[str]:
    return {
        str(p.relative_to(target))
        for p in target.rglob("*")
        if p.is_file() and ".git" not in p.relative_to(target).parts
    }


def commits(target: Path) -> list[str]:
    return git(target, "log", "--format=%s").split("\n")[:-1] if has_commit(target) else []


def has_commit(target: Path) -> bool:
    return (
        subprocess.run(["git", "rev-parse", "-q", "--verify", "HEAD"], cwd=target).returncode == 0
    )


def test_an_empty_repo_gets_one_commit_and_the_github_step_and_a_second_run_changes_nothing(
    tmp_path: Path, fake_gh: FakeGh, released: FixtureTemplate, run_apply: Apply
) -> None:
    target = prepare(tmp_path, fake_gh)

    first = run_apply(target, released.path, env=fake_gh.env)

    assert first.returncode == 0, first.stderr
    assert commits(target) == [COMMIT_SUBJECT]
    assert git(target, "log", "-1", "--format=%an <%ae>").strip() == "Test <test@example.com>"
    assert set(git(target, "ls-tree", "-r", "--name-only", "HEAD").split()) == tree(target)
    assert ".copier-answers.yml" in tree(target)
    assert git(target, "status", "--porcelain").strip() == ""
    head = git(target, "rev-parse", "HEAD").strip()
    assert git(fake_gh.root / "origin.git", "rev-parse", "refs/heads/main").strip() == head
    report: list[dict[str, Any]] = json.loads(first.stdout)["results"]
    assert report[0]["operation"] == {"op": "push", "branch": "main"}
    assert {r["outcome"] for r in report} == {"applied"}
    assert report[-1]["operation"]["op"] == "create_ruleset"

    fake_gh.clear_log()
    second = run_apply(target, released.path, env=fake_gh.env)

    assert second.returncode == 0, second.stderr
    assert commits(target) == [COMMIT_SUBJECT]
    assert second.stdout == ""
    assert "existing repo" in second.stderr
    assert fake_gh.calls() == [] and fake_gh.git_calls() == []
    assert git(target, "status", "--porcelain").strip() == ""


def empty_state_file(tmp_path: Path) -> Path:
    state = json.loads((FIXTURES / "new-repo.json").read_text())
    path = tmp_path / "empty-state.json"
    path.write_text(json.dumps({**state, "empty": True}))
    return path


def written_files(report: dict[str, Any]) -> set[str]:
    return set(report["files"])


def dry_run_then_live(
    tmp_path: Path,
    fake_gh: FakeGh,
    released: FixtureTemplate,
    run_apply: Apply,
    extra: tuple[str, ...],
) -> None:
    target = prepare(tmp_path, fake_gh)

    dry = run_apply(target, released.path, "--dry-run", *extra, env=fake_gh.env)

    assert dry.returncode == 0, dry.stderr
    assert tree(target) == set()  # the dry run wrote nothing
    assert not has_commit(target)
    assert "no commit yet" not in dry.stderr
    preview = json.loads(dry.stdout)
    assert preview["plan"]["operations"][0] == {"op": "push", "branch": "main"}
    assert preview["plan"]["operations"][-1]["op"] == "create_ruleset"
    assert fake_gh.writes() == [] and fake_gh.git_calls() == []

    live = run_apply(target, released.path, env=fake_gh.env)

    assert live.returncode == 0, live.stderr
    assert written_files(preview) == tree(target)  # the dry run named the files of the live run
    assert commits(target) == [COMMIT_SUBJECT]


def test_a_dry_run_with_a_state_file_writes_nothing_and_a_live_run_can_follow(
    tmp_path: Path, fake_gh: FakeGh, released: FixtureTemplate, run_apply: Apply
) -> None:
    state = empty_state_file(tmp_path)

    dry_run_then_live(tmp_path, fake_gh, released, run_apply, ("--state", str(state)))


def test_a_live_read_dry_run_writes_nothing_and_a_live_run_can_follow(
    tmp_path: Path, fake_gh: FakeGh, released: FixtureTemplate, run_apply: Apply
) -> None:
    dry_run_then_live(tmp_path, fake_gh, released, run_apply, ())


def commit_something(target: Path) -> None:
    (target / "README.md").write_text("# project\n")
    git(target, "add", "-A")
    git(target, "commit", "-q", "-m", "chore: start")


def test_an_existing_repo_gets_files_and_no_commit_and_no_github_call(
    tmp_path: Path, fake_gh: FakeGh, released: FixtureTemplate, run_apply: Apply
) -> None:
    for name, github_empty in (("full", False), ("unpushed", True)):
        case = tmp_path / name
        case.mkdir()
        target = prepare(case, fake_gh)
        commit_something(target)
        if not github_empty:
            fake_gh.update(lambda state: state.pop("bare"))
        fake_gh.clear_log()

        result = run_apply(target, released.path, env=fake_gh.env)

        assert result.returncode == 0, (name, result.stderr)
        assert commits(target) == ["chore: start"], name
        assert ".github/bootstrap.yml" in tree(target)
        assert result.stdout == "" and "existing repo" in result.stderr, name
        assert fake_gh.calls() == [] and fake_gh.git_calls() == [], name


def test_no_origin_commits_and_skips_the_github_step(
    tmp_path: Path, fake_gh: FakeGh, released: FixtureTemplate, run_apply: Apply
) -> None:
    target = prepare(tmp_path, fake_gh, origin=False)

    dry = run_apply(target, released.path, "--dry-run", env=fake_gh.env)

    assert dry.returncode == 0, dry.stderr
    preview = json.loads(dry.stdout)
    assert preview["plan"] is None and ".github/bootstrap.yml" in preview["files"]
    assert "no origin" in dry.stderr
    assert tree(target) == set() and not has_commit(target)

    live = run_apply(target, released.path, env=fake_gh.env)

    assert live.returncode == 0, live.stderr
    assert commits(target) == [COMMIT_SUBJECT]
    assert live.stdout == "" and "no origin" in live.stderr
    assert fake_gh.calls() == [] and fake_gh.git_calls() == []


def make_orphan_head(target: Path, fake_gh: FakeGh) -> None:
    git(target, "commit", "-q", "--allow-empty", "-m", "chore: start")
    git(target, "symbolic-ref", "HEAD", "refs/heads/unborn")


def make_github_not_empty(target: Path, fake_gh: FakeGh) -> None:
    fake_gh.update(lambda state: state.pop("bare"))


def make_branch_trunk(target: Path, fake_gh: FakeGh) -> None:
    git(target, "symbolic-ref", "HEAD", "refs/heads/trunk")


def make_failing_read(target: Path, fake_gh: FakeGh) -> None:
    fake_gh.update(
        lambda state: state.update(
            errors={"repos/octo/widgets": {"status": 500, "message": "Server Error"}}
        )
    )


STOPS = {
    "local branch is not the default branch": (make_branch_trunk, (), {}, "git branch -m main"),
    "GitHub is not empty": (make_github_not_empty, (), {}, "already has a branch"),
    "a state file without a dry run": (
        lambda target, fake_gh: None,
        ("--state", "STATE"),
        {},
        "only works with --dry-run",
    ),
    "the first read fails": (make_failing_read, (), {}, "Server Error"),
    "a bad push timeout": (
        lambda target, fake_gh: None,
        (),
        {"BOOTSTRAP_PUSH_TIMEOUT": "abc"},
        "BOOTSTRAP_PUSH_TIMEOUT",
    ),
    "an orphan checkout": (make_orphan_head, (), {}, "has no commit, but the repo has branches"),
}


def test_apply_stops_before_it_writes_anything(
    tmp_path: Path, fake_gh: FakeGh, released: FixtureTemplate, run_apply: Apply
) -> None:
    for name, (setup, extra, env, message) in STOPS.items():
        case = tmp_path / name.replace(" ", "-")
        case.mkdir()
        target = prepare(case, fake_gh)
        setup(target, fake_gh)
        state = empty_state_file(case)
        args = tuple(str(state) if a == "STATE" else a for a in extra)

        result = run_apply(target, released.path, *args, env={**fake_gh.env, **env})

        assert result.returncode == 1, (name, result.stdout)
        assert message in result.stderr, (name, result.stderr)
        assert tree(target) == set(), name
        assert fake_gh.writes() == [] and fake_gh.git_calls() == [], name
        if name != "an orphan checkout":
            assert not has_commit(target), name
        fake_gh.clear_log()


def update_state(fake_gh: FakeGh, **switches: Any) -> None:
    fake_gh.update(lambda state: state.update(switches))


def hide_with_global_ignore(target: Path, tmp_path: Path, *patterns: str) -> None:
    """A global ignore, as a user has for `.claude/`. Git config in the repo points to it."""
    excludes = tmp_path / "excludes"
    excludes.write_text("\n".join(patterns) + "\n")
    git(target, "config", "core.excludesFile", str(excludes))


def test_a_dry_run_of_an_update_lists_the_files_and_the_deleted_files_and_writes_nothing(
    tmp_path: Path, fake_gh: FakeGh, template: FixtureTemplate, run_apply: Apply
) -> None:
    template.release("v0.1.0", {"template/old.txt": "old\n"})
    target = prepare(tmp_path, fake_gh)
    first = run_apply(target, template.path, env=fake_gh.env)
    assert first.returncode == 0, first.stderr
    template.release("v0.2.0", {"template/old.txt": None, "template/new.txt": "new\n"})
    before = tree(target)

    fake_gh.clear_log()
    dry = run_apply(
        target, template.path, "--dry-run", "--template-version", "v0.2.0", env=fake_gh.env
    )

    assert dry.returncode == 0, dry.stderr
    assert fake_gh.calls() == []
    preview = json.loads(dry.stdout)
    assert "new.txt" in preview["files"]
    assert preview["deleted"] == ["old.txt"]
    assert preview["plan"] is None and "existing repo" in dry.stderr
    assert tree(target) == before
    assert git(target, "status", "--porcelain").strip() == ""


def test_a_dry_run_predicts_the_failures_of_the_live_run(
    tmp_path: Path, fake_gh: FakeGh, released: FixtureTemplate, run_apply: Apply
) -> None:
    ignored = prepare(tmp_path, fake_gh, "ignored")
    hide_with_global_ignore(ignored, tmp_path, ".github/")
    released.release("v0.2.0", {"sections/agents/tdd.md": None})
    cases = {
        "a written path that a global ignore hides": (
            ignored,
            ("--template-version", "v0.1.0"),  # the release before the broken one
            "Change your ignore rules",  # the stop, not the warning of an existing repo
        ),
        "a template with a missing section file": (
            prepare(tmp_path, fake_gh, "missing"),
            ("--template-version", "v0.2.0"),
            "no section file",
        ),
    }
    for name, (target, extra, message) in cases.items():
        for flags in (("--dry-run",), ()):
            result = run_apply(target, released.path, *flags, *extra, env=fake_gh.env)

            assert result.returncode == 1, (name, flags, result.stdout)
            assert message in result.stderr, (name, flags, result.stderr)
            assert tree(target) == set() and not has_commit(target), (name, flags)


def test_an_existing_repo_with_a_hidden_written_path_is_warned_and_not_stopped(
    tmp_path: Path, fake_gh: FakeGh, released: FixtureTemplate, run_apply: Apply
) -> None:
    target = prepare(tmp_path, fake_gh)
    commit_something(target)
    hide_with_global_ignore(target, tmp_path, "AGENTS.md")

    dry = run_apply(target, released.path, "--dry-run", env=fake_gh.env)
    live = run_apply(target, released.path, env=fake_gh.env)

    assert dry.returncode == 0 and live.returncode == 0, (dry.stderr, live.stderr)
    assert json.loads(dry.stdout)["ignored"] == ["AGENTS.md"]
    assert "AGENTS.md" in dry.stderr and "AGENTS.md" in live.stderr
    assert commits(target) == ["chore: start"]


def test_a_skip_if_exists_file_that_exists_is_neither_listed_nor_committed(
    tmp_path: Path, fake_gh: FakeGh, template: FixtureTemplate, run_apply: Apply
) -> None:
    copier_yml = yaml.safe_load((template.path / "copier.yml").read_text())
    copier_yml["_skip_if_exists"] = [*copier_yml.get("_skip_if_exists", []), "policy.md"]
    template.release(
        "v0.1.0",
        {"copier.yml": yaml.safe_dump(copier_yml, sort_keys=False), "template/policy.md": "x\n"},
    )
    target = prepare(tmp_path, fake_gh)
    (target / "policy.md").write_text("mine\n")
    hide_with_global_ignore(target, tmp_path, "policy.md")  # so the worktree counts as clean

    dry = run_apply(target, template.path, "--dry-run", env=fake_gh.env)

    assert dry.returncode == 0, dry.stderr
    assert "policy.md" not in json.loads(dry.stdout)["files"]

    live = run_apply(target, template.path, env=fake_gh.env)

    assert live.returncode == 0, live.stderr
    assert (target / "policy.md").read_text() == "mine\n"
    assert "policy.md" not in git(target, "ls-tree", "-r", "--name-only", "HEAD").split()


def test_a_failed_push_keeps_the_commit_and_github_finishes_the_work(
    tmp_path: Path, fake_gh: FakeGh, released: FixtureTemplate, run_apply: Apply
) -> None:
    target = prepare(tmp_path, fake_gh)
    stderr = (
        "fatal: unable to access 'https://x-access-token:SECRET@github.com/octo/widgets.git/'\n"
    )
    update_state(fake_gh, push_error={"stderr": stderr, "code": 128})

    first = run_apply(target, released.path, env=fake_gh.env)

    assert first.returncode == 1
    assert commits(target) == [COMMIT_SUBJECT]
    report = json.loads(first.stdout)["results"]
    assert [r["outcome"] for r in report][:2] == ["failed", "not_run"]
    assert "SECRET" not in first.stdout + first.stderr
    assert fake_gh.writes() == []

    fake_gh.update(lambda state: state.pop("push_error"))
    second = run_github(target, env=fake_gh.env)  # the recovery: `bootstrap github .`

    assert second.returncode == 0, second.stderr
    assert commits(target) == [COMMIT_SUBJECT]  # no new commit
    assert {r["outcome"] for r in json.loads(second.stdout)["results"]} == {"applied"}
    assert json.loads(run_github(target, env=fake_gh.env).stdout) == {
        "results": [],
        "plan_skipped": [],
    }


def test_a_commit_that_a_hook_rejects_leaves_the_files_staged_and_says_how_to_go_on(
    tmp_path: Path, fake_gh: FakeGh, released: FixtureTemplate, run_apply: Apply
) -> None:
    target = prepare(tmp_path, fake_gh)
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    hook = hooks / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'pre-commit says no' >&2\nexit 1\n")
    hook.chmod(0o755)
    git(target, "config", "core.hooksPath", str(hooks))

    result = run_apply(target, released.path, env=fake_gh.env)

    assert result.returncode == 1
    assert "git commit failed" in result.stderr and "bootstrap github" in result.stderr
    assert not has_commit(target)
    assert git(target, "diff", "--cached", "--name-only").split()  # the files are staged
    assert fake_gh.writes() == [] and fake_gh.git_calls() == []


def test_no_git_identity_stops_the_commit_and_does_not_guess_one(
    tmp_path: Path, fake_gh: FakeGh, released: FixtureTemplate, run_apply: Apply
) -> None:
    target = prepare(tmp_path, fake_gh)
    identity = (
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
        "EMAIL",
    )

    result = run_apply(target, released.path, env=fake_gh.env, unset=identity)

    assert result.returncode == 1
    assert "auto-detection is disabled" in result.stderr
    assert not has_commit(target)
    assert fake_gh.writes() == []


def test_ctrl_c_during_the_push_says_how_to_go_on_and_leaves_no_process(
    tmp_path: Path, fake_gh: FakeGh, released: FixtureTemplate
) -> None:
    target = prepare(tmp_path, fake_gh)
    update_state(fake_gh, push_sleep=30)
    env = {**os.environ, **GIT_ENV, **fake_gh.env}
    process = subprocess.Popen(
        [str(BOOTSTRAP), "apply", str(target), "--template", str(released.path)],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    for _ in range(300):
        if fake_gh.pid_path.exists():
            break
        time.sleep(0.1)
    assert fake_gh.pid_path.exists(), "the push did not start"

    process.send_signal(signal.SIGINT)
    _, err = process.communicate(timeout=60)

    assert process.returncode == 130, err
    assert "git commit" in err and "bootstrap github" in err and "Traceback" not in err, err
    assert commits(target) == [COMMIT_SUBJECT]
    assert fake_gh.push_group_gone()


def test_an_existing_repo_does_not_read_a_state_file(
    tmp_path: Path, fake_gh: FakeGh, released: FixtureTemplate, run_apply: Apply
) -> None:
    target = prepare(tmp_path, fake_gh)
    commit_something(target)

    result = run_apply(
        target,
        released.path,
        "--dry-run",
        "--state",
        str(tmp_path / "missing.json"),
        env=fake_gh.env,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["plan"] is None
    assert fake_gh.calls() == []


def test_a_template_without_a_settings_file_stops_before_any_write_on_a_live_run(
    tmp_path: Path, fake_gh: FakeGh, released: FixtureTemplate, run_apply: Apply
) -> None:
    released.release("v0.2.0", {"template/.github/bootstrap.yml.jinja": None})
    target = prepare(tmp_path, fake_gh)

    for flags in (("--dry-run",), ()):
        result = run_apply(target, released.path, *flags, env=fake_gh.env)

        assert result.returncode == 1, flags
        assert "the template has no .github/bootstrap.yml" in result.stderr, flags
        assert tree(target) == set() and not has_commit(target), flags


def test_a_file_name_that_looks_like_a_pathspec_is_staged_as_a_name(
    tmp_path: Path, fake_gh: FakeGh, template: FixtureTemplate, run_apply: Apply
) -> None:
    template.release("v0.1.0", {"template/:magic.txt": "x\n", "template/a[b].txt": "y\n"})
    target = prepare(tmp_path, fake_gh)

    result = run_apply(target, template.path, env=fake_gh.env)

    assert result.returncode == 0, result.stderr
    committed = git(target, "ls-tree", "-r", "--name-only", "HEAD").split("\n")
    assert ":magic.txt" in committed and "a[b].txt" in committed


def test_a_file_that_the_templates_own_gitignore_hides_is_written_and_not_committed(
    tmp_path: Path, fake_gh: FakeGh, template: FixtureTemplate, run_apply: Apply
) -> None:
    template.release(
        # `.jinja` keeps the template repo from ignoring the file. Copier renders `hidden.txt`.
        "v0.1.0",
        {"template/.gitignore": "hidden.txt\n", "template/hidden.txt.jinja": "secret\n"},
    )
    target = prepare(tmp_path, fake_gh)

    result = run_apply(target, template.path, env=fake_gh.env)

    assert result.returncode == 0, result.stderr
    assert (target / "hidden.txt").read_text() == "secret\n"
    committed = git(target, "ls-tree", "-r", "--name-only", "HEAD").split("\n")
    assert ".gitignore" in committed and "hidden.txt" not in committed
    assert git(target, "status", "--porcelain").strip() == ""


def test_a_deleted_list_names_only_files_that_the_target_still_has(
    tmp_path: Path, fake_gh: FakeGh, template: FixtureTemplate, run_apply: Apply
) -> None:
    template.release("v0.1.0", {"template/old.txt": "old\n"})
    target = prepare(tmp_path, fake_gh)
    assert run_apply(target, template.path, env=fake_gh.env).returncode == 0
    git(target, "rm", "-q", "old.txt")
    git(target, "commit", "-q", "-m", "chore: remove old.txt")
    template.release("v0.2.0", {"template/old.txt": None})

    dry = run_apply(
        target, template.path, "--dry-run", "--template-version", "v0.2.0", env=fake_gh.env
    )

    assert dry.returncode == 0, dry.stderr
    assert json.loads(dry.stdout)["deleted"] == []

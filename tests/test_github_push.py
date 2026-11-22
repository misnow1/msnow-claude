"""The first push of an empty repo, in a live run of the `github` subcommand.

A fake `gh` plays GitHub and a fake `git` sends the push to a local bare repo, so these tests
never reach the real service.
"""

import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
from conftest import (
    BOOTSTRAP,
    FIXTURES,
    GIT_ENV,
    FakeGh,
    add_origin,
    commit_all,
    fake_state,
    git,
    make_repo,
    repo_with_settings,
    run_github,
)

from bootstrap.gitutil import kill_group

EMPTY_REPORT: dict[str, list[Any]] = {"results": [], "plan_skipped": []}


def prepare(tmp_path: Path, fake_gh: FakeGh, commit: bool = True) -> tuple[Path, Path]:
    """A local repo with a commit on main, and an empty fake GitHub with a bare origin."""
    state = fake_state(json.loads((FIXTURES / "new-repo.json").read_text()))
    fake_gh.install(state)
    bare = fake_gh.bare()
    target = add_origin(repo_with_settings(tmp_path / "repo"))
    git(target, "symbolic-ref", "HEAD", "refs/heads/main")
    if commit:
        commit_all(target, "initial")
    return target, bare


def run(
    target: Path, fake_gh: FakeGh, *extra: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    result = run_github(target, *extra, env={**fake_gh.env, **(env or {})})
    fake_gh.assert_repo_scoped()
    return result


def results(proc: subprocess.CompletedProcess[str]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = json.loads(proc.stdout)["results"]
    return found


def test_an_empty_repo_is_pushed_before_the_rest_and_a_second_run_changes_nothing(
    tmp_path: Path, fake_gh: FakeGh
) -> None:
    target, bare = prepare(tmp_path, fake_gh)
    commit = git(target, "rev-parse", "refs/heads/main").strip()

    first = run(target, fake_gh)

    assert first.returncode == 0, first.stderr
    report = results(first)
    assert [r["operation"]["op"] for r in report][:2] == ["push", "update_repository"]
    assert report[-1]["operation"]["op"] == "create_ruleset"
    assert {r["outcome"] for r in report} == {"applied"}
    assert commit in report[0]["reason"]
    assert git(bare, "rev-parse", "refs/heads/main").strip() == commit
    [pushed] = fake_gh.git_calls()
    assert pushed["args"][-2:] == ["origin", "refs/heads/main:refs/heads/main"]

    fake_gh.clear_log()
    second = run(target, fake_gh)

    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout) == EMPTY_REPORT
    assert fake_gh.writes() == []
    assert fake_gh.git_calls() == []


def log_text(fake_gh: FakeGh) -> str:
    """Everything that the fakes logged, to look for a secret."""
    parts = [fake_gh.log_path, fake_gh.git_log_path]
    return "".join(p.read_text() for p in parts if p.exists())


def update(fake_gh: FakeGh, **switches: Any) -> None:
    fake_gh.update(lambda state: state.update(switches))


def alive(pgid: int) -> bool:
    """True while a process of the group is left."""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # on macOS, a group that holds only a zombie
        return True
    return True


def gone(fake_gh: FakeGh) -> bool:
    """True when the process group of the fake git is gone, after a short wait."""
    pgid = int(fake_gh.pid_path.read_text())
    for _ in range(100):
        if not alive(pgid):
            return True
        time.sleep(0.1)
    return False


def test_no_commit_stops_a_live_run_before_any_write_and_a_dry_run_only_warns(
    tmp_path: Path, fake_gh: FakeGh
) -> None:
    target, _ = prepare(tmp_path, fake_gh, commit=False)

    live = run(target, fake_gh)

    assert live.returncode == 1
    assert "no commit" in live.stderr
    assert fake_gh.writes() == []
    assert fake_gh.git_calls() == []

    dry = run(target, fake_gh, "--dry-run")

    assert dry.returncode == 0, dry.stderr
    assert json.loads(dry.stdout)["operations"][0] == {"op": "push", "branch": "main"}
    assert "no commit yet" in dry.stderr
    assert fake_gh.git_calls() == []


def test_a_dry_run_with_a_commit_does_not_warn_and_does_not_push(
    tmp_path: Path, fake_gh: FakeGh
) -> None:
    target, bare = prepare(tmp_path, fake_gh)

    dry = run(target, fake_gh, "--dry-run")

    assert dry.returncode == 0, dry.stderr
    assert "no commit" not in dry.stderr
    assert fake_gh.git_calls() == []
    assert git(bare, "for-each-ref").strip() == ""


def test_a_local_branch_other_than_the_default_stops_a_live_run_before_any_write(
    tmp_path: Path, fake_gh: FakeGh
) -> None:
    target, _ = prepare(tmp_path, fake_gh)
    git(target, "branch", "-m", "trunk")

    result = run(target, fake_gh)

    assert result.returncode == 1
    assert "git branch -m main" in result.stderr
    assert fake_gh.writes() == []
    assert fake_gh.git_calls() == []


FAILURES = {
    "https with a token": (
        "fatal: unable to access 'https://x-access-token:SECRET@github.com/octo/widgets.git/': "
        "The requested URL returned error: 403\n",
        "<url>",
    ),
    "scp-like": ("fatal: Could not read from 'git@github.com:octo/widgets.git'.\n", "<url>"),
    "userinfo without a scheme": (
        "error: failed to push some refs to 'x-access-token:SECRET@github.com/octo/widgets'\n",
        "<url>",
    ),
    "userinfo with no path": (
        "fatal: bad URL x-access-token:SECRET@github.com\n",
        "<url>",
    ),
    "quoted userinfo with no path": ("fatal: bad URL 'user:SECRET@github.com'\n", "<url>"),
    "trace output": (
        "11:02:03.1 trace: run_command: git-remote-https https://x:SECRET@github.com/o/r.git\n"
        "fatal: the remote end hung up unexpectedly\n",
        "hung up",
    ),
}


@pytest.mark.parametrize(("stderr", "shown"), FAILURES.values(), ids=FAILURES)
def test_a_failed_push_shows_a_redacted_reason_and_stops_the_run(
    tmp_path: Path, fake_gh: FakeGh, stderr: str, shown: str
) -> None:
    target, _ = prepare(tmp_path, fake_gh)
    update(fake_gh, push_error={"stderr": stderr, "code": 128})

    result = run(target, fake_gh)

    assert result.returncode == 1
    report = results(result)
    assert [r["outcome"] for r in report][:2] == ["failed", "not_run"]
    assert shown in report[0]["reason"]
    assert "SECRET" not in result.stdout + result.stderr + log_text(fake_gh)
    assert "github.com" not in report[0]["reason"]
    assert fake_gh.writes() == []


def test_git_does_not_see_the_trace_variables_and_speaks_english(
    tmp_path: Path, fake_gh: FakeGh
) -> None:
    target, _ = prepare(tmp_path, fake_gh)
    noisy = {
        "GIT_TRACE": "1",
        "GIT_CURL_VERBOSE": "1",
        "GIT_TRACE_REDACT": "0",
        "GIT_TRACE2": "/tmp/trace2",
        "LC_ALL": "de_DE.UTF-8",
        "LANGUAGE": "de",
    }

    result = run(target, fake_gh, env=noisy)

    assert result.returncode == 0, result.stderr
    [pushed] = fake_gh.git_calls()
    assert (pushed["GIT_TRACE"], pushed["GIT_CURL_VERBOSE"], pushed["GIT_TRACE_REDACT"]) == (
        None,
        None,
        None,
    )
    # A trace2 target in the config would still write, so the environment turns trace2 off.
    assert (pushed["GIT_TRACE2"], pushed["GIT_TRACE2_EVENT"], pushed["GIT_TRACE2_PERF"]) == (
        "0",
        "0",
        "0",
    )
    assert (pushed["LC_ALL"], pushed["LANGUAGE"], pushed["LC_MESSAGES"]) == (None, None, "C")


REJECTIONS = {
    "fetch first": (
        "To https://github.com/octo/widgets.git\n"
        "!\trefs/heads/main:refs/heads/main\t[rejected] (fetch first)\nDone\n",
        "[rejected] (fetch first)",
    ),
    "workflow scope": (
        "To https://github.com/octo/widgets.git\n"
        "!\trefs/heads/main:refs/heads/main\t[remote rejected] (refusing to allow an OAuth App "
        "to create or update workflow `.github/workflows/ci.yml` without `workflow` scope)\n",
        "without `workflow` scope",
    ),
}


@pytest.mark.parametrize(("stdout", "shown"), REJECTIONS.values(), ids=REJECTIONS)
def test_a_rejection_shows_the_reason_from_the_porcelain_line(
    tmp_path: Path, fake_gh: FakeGh, stdout: str, shown: str
) -> None:
    target, _ = prepare(tmp_path, fake_gh)
    update(fake_gh, push_stdout={"stdout": stdout, "code": 1})

    result = run(target, fake_gh)

    assert result.returncode == 1
    assert shown in results(result)[0]["reason"]
    assert "https://" not in result.stdout + result.stderr


NO_RESULT = {
    "no line, exit 0": ({"stdout": "Done\n", "code": 0}, "no result for the branch"),
    "new branch, exit 1": (
        {"stdout": "*\trefs/heads/main:refs/heads/main\t[new branch]\n", "code": 1},
        "but git exited 1",
    ),
}


@pytest.mark.parametrize(("switch", "shown"), NO_RESULT.values(), ids=NO_RESULT)
def test_a_push_with_no_result_line_or_a_late_error_is_a_failure(
    tmp_path: Path, fake_gh: FakeGh, switch: dict[str, Any], shown: str
) -> None:
    target, _ = prepare(tmp_path, fake_gh)
    update(fake_gh, push_stdout=switch)

    result = run(target, fake_gh)

    assert result.returncode == 1
    assert shown in results(result)[0]["reason"]
    assert results(result)[1]["outcome"] == "not_run"


def test_output_that_is_not_utf8_still_gives_a_reason(tmp_path: Path, fake_gh: FakeGh) -> None:
    target, _ = prepare(tmp_path, fake_gh)
    stdout = "!\trefs/heads/main:refs/heads/main\t[remote rejected] (caf\u00e9 policy)\n"
    update(fake_gh, push_stdout={"stdout_latin1": stdout, "code": 1})

    result = run(target, fake_gh)

    assert result.returncode == 1
    assert "unexpected error" not in result.stdout
    assert "policy" in results(result)[0]["reason"]


def test_a_branch_that_has_a_tag_of_the_same_name_is_pushed(
    tmp_path: Path, fake_gh: FakeGh
) -> None:
    target, bare = prepare(tmp_path, fake_gh)
    git(target, "tag", "main")

    result = run(target, fake_gh)

    assert result.returncode == 0, result.stderr
    assert refs(bare) == ["refs/heads/main"]


def test_a_push_that_times_out_fails_with_an_unknown_result_and_leaves_no_process(
    tmp_path: Path, fake_gh: FakeGh
) -> None:
    target, bare = prepare(tmp_path, fake_gh)
    update(fake_gh, push_sleep=30)
    started = time.monotonic()

    result = run(target, fake_gh, env={"BOOTSTRAP_PUSH_TIMEOUT": "1"})

    assert time.monotonic() - started < 20  # the kill, not the end of the sleep, ended the push
    assert result.returncode == 1
    assert "the result is unknown" in results(result)[0]["reason"]
    assert results(result)[1]["outcome"] == "not_run"
    assert gone(fake_gh)
    assert refs(bare) == []  # the push was killed before it could land


def test_a_push_that_landed_before_a_timeout_converges_on_the_next_runs(
    tmp_path: Path, fake_gh: FakeGh
) -> None:
    target, bare = prepare(tmp_path, fake_gh)
    update(fake_gh, push_sleep_after=30)

    first = run(target, fake_gh, env={"BOOTSTRAP_PUSH_TIMEOUT": "5"})

    assert first.returncode == 1
    assert "the result is unknown" in results(first)[0]["reason"]
    assert git(bare, "rev-parse", "refs/heads/main").strip()
    update(fake_gh, push_sleep_after=0)

    second = run(target, fake_gh)

    assert second.returncode == 0, second.stderr
    assert "push" not in [r["operation"]["op"] for r in results(second)]
    assert {r["outcome"] for r in results(second)} == {"applied"}
    assert json.loads(run(target, fake_gh).stdout) == EMPTY_REPORT


def test_a_bad_push_timeout_stops_the_command_before_any_write(
    tmp_path: Path, fake_gh: FakeGh
) -> None:
    target, _ = prepare(tmp_path, fake_gh)

    for value in ("abc", "0", "-5", "99999", ""):
        result = run(target, fake_gh, env={"BOOTSTRAP_PUSH_TIMEOUT": value})

        assert result.returncode == 1, value
        assert "BOOTSTRAP_PUSH_TIMEOUT" in result.stderr, value
        assert fake_gh.writes() == [] and fake_gh.git_calls() == [], value


def test_a_push_that_loses_the_race_for_the_default_branch_is_reported(
    tmp_path: Path, fake_gh: FakeGh
) -> None:
    target, _ = prepare(tmp_path, fake_gh)
    update(fake_gh, default_after_first_push="other")

    result = run(target, fake_gh)

    assert result.returncode == 1
    reason = results(result)[0]["reason"]
    assert reason.startswith("pushed ") and "but GitHub's default branch is other" in reason
    assert results(result)[1]["outcome"] == "not_run"
    assert fake_gh.writes() == []


def test_a_default_branch_that_cannot_be_read_after_the_push_is_reported(
    tmp_path: Path, fake_gh: FakeGh
) -> None:
    target, _ = prepare(tmp_path, fake_gh)
    update(
        fake_gh,
        errors={"repos/octo/widgets": {"status": 500, "message": "Server Error", "after": 1}},
    )

    result = run(target, fake_gh)

    assert result.returncode == 1
    reason = results(result)[0]["reason"]
    assert reason.startswith("pushed ") and "cannot read the default branch" in reason


@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM, signal.SIGHUP])
def test_an_interrupt_during_a_push_kills_git_and_says_so(
    tmp_path: Path, fake_gh: FakeGh, sig: signal.Signals
) -> None:
    target, _ = prepare(tmp_path, fake_gh)
    update(fake_gh, push_sleep=30)
    env = {**os.environ, **GIT_ENV, **fake_gh.env}
    process = subprocess.Popen(
        [str(BOOTSTRAP), "github", str(target)],
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

    process.send_signal(sig)
    _, err = process.communicate(timeout=60)

    assert process.returncode == 130, err
    assert "interrupted" in err and "Run again" in err, err
    assert "Traceback" not in err, err
    assert gone(fake_gh), "a process of the push is still alive"


def test_killing_a_group_whose_only_process_has_exited_does_not_raise() -> None:
    process: subprocess.Popen[str] = subprocess.Popen(["true"], process_group=0, text=True)
    time.sleep(0.5)  # it has exited, and nobody has reaped it

    kill_group(process)  # on macOS, killpg on such a group raises PermissionError

    process.wait()


def refs(repo: Path) -> list[str]:
    return git(repo, "for-each-ref", "--format=%(refname)").split()


def make_submodule(tmp_path: Path, target: Path) -> Path:
    """A submodule whose origin is a second bare repo, with a commit that is not pushed."""
    sub_bare = tmp_path / "sub.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(sub_bare))
    seed = make_repo(tmp_path / "seed")
    git(seed, "symbolic-ref", "HEAD", "refs/heads/main")
    (seed / "a.txt").write_text("a")
    commit_all(seed)
    git(seed, "push", "-q", str(sub_bare), "main")
    git(target, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(sub_bare), "sub")
    (target / "sub" / "b.txt").write_text("b")
    commit_all(target / "sub", "not pushed")
    commit_all(target, "add the submodule")
    return sub_bare


def test_the_premises_of_the_config_tests_hold(tmp_path: Path, fake_gh: FakeGh) -> None:
    target, bare = prepare(tmp_path, fake_gh)
    sub_bare = make_submodule(tmp_path, target)
    before = git(sub_bare, "rev-parse", "main")
    push = ["push", "--porcelain", str(bare), "refs/heads/main:refs/heads/main"]

    signed = subprocess.run(["git", "-c", "push.gpgSign=true", *push], cwd=target, check=False)
    recursive = subprocess.run(
        ["git", "-c", "push.recurseSubmodules=on-demand", *push],
        cwd=target,
        check=False,
        capture_output=True,
        env={**os.environ, **GIT_ENV},
    )

    assert signed.returncode != 0  # a plain push with push.gpgSign fails against this bare repo
    assert recursive.returncode == 0
    assert (
        git(sub_bare, "rev-parse", "main") != before
    )  # and one that recurses pushes the submodule


CONFIGS = {
    "remote.origin.push maps main to another name": (
        "remote.origin.push",
        "refs/heads/main:refs/heads/other",
    ),
    "push.default is nothing": ("push.default", "nothing"),
    "push.gpgSign": ("push.gpgSign", "true"),
}


@pytest.mark.parametrize(("key", "value"), CONFIGS.values(), ids=CONFIGS)
def test_config_that_would_change_the_push_does_not(
    tmp_path: Path, fake_gh: FakeGh, key: str, value: str
) -> None:
    target, bare = prepare(tmp_path, fake_gh)
    git(target, "config", key, value)

    result = run(target, fake_gh)

    assert result.returncode == 0, result.stderr
    assert refs(bare) == ["refs/heads/main"]


def test_follow_tags_does_not_send_a_tag(tmp_path: Path, fake_gh: FakeGh) -> None:
    target, bare = prepare(tmp_path, fake_gh)
    git(target, "tag", "-a", "v1", "-m", "a release")
    git(target, "config", "push.followTags", "true")

    result = run(target, fake_gh)

    assert result.returncode == 0, result.stderr
    assert refs(bare) == ["refs/heads/main"]


def test_recurse_submodules_does_not_push_a_submodule_to_its_own_remote(
    tmp_path: Path, fake_gh: FakeGh
) -> None:
    target, bare = prepare(tmp_path, fake_gh)
    sub_bare = make_submodule(tmp_path, target)
    before = git(sub_bare, "rev-parse", "main")
    git(target, "config", "push.recurseSubmodules", "on-demand")

    result = run(target, fake_gh)

    assert result.returncode == 0, result.stderr
    assert refs(bare) == ["refs/heads/main"]
    assert git(sub_bare, "rev-parse", "main") == before


def test_a_mirror_remote_fails_closed(tmp_path: Path, fake_gh: FakeGh) -> None:
    target, bare = prepare(tmp_path, fake_gh)
    git(target, "config", "remote.origin.mirror", "true")

    result = run(target, fake_gh)

    assert result.returncode == 1
    assert results(result)[0]["outcome"] == "failed"
    assert refs(bare) == []
    assert fake_gh.writes() == []


def test_killing_a_group_reaps_the_process_so_no_zombie_is_left() -> None:
    process: subprocess.Popen[str] = subprocess.Popen(["sleep", "30"], process_group=0, text=True)

    kill_group(process)

    # Python's Popen does not wait for a child after Ctrl-C. A zombie would stay until the
    # init process of the machine or the container reaps it, and some containers never do.
    assert process.returncode is not None
    with pytest.raises(ChildProcessError):
        os.waitpid(process.pid, os.WNOHANG)


def test_a_hangup_that_the_user_ignored_does_not_stop_the_push(
    tmp_path: Path, fake_gh: FakeGh
) -> None:
    """Under `nohup`, a hangup must not kill a push that the user asked to keep running."""
    target, bare = prepare(tmp_path, fake_gh)
    update(fake_gh, push_sleep=3)
    env = {**os.environ, **GIT_ENV, **fake_gh.env}
    process = subprocess.Popen(
        [str(BOOTSTRAP), "github", str(target)],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        preexec_fn=lambda: signal.signal(signal.SIGHUP, signal.SIG_IGN),  # noqa: PLW1509
    )
    for _ in range(300):
        if fake_gh.pid_path.exists():
            break
        time.sleep(0.1)
    assert fake_gh.pid_path.exists(), "the push did not start"

    process.send_signal(signal.SIGHUP)
    out, err = process.communicate(timeout=60)

    assert process.returncode == 0, err
    assert "interrupted" not in err
    assert refs(bare) == ["refs/heads/main"]
    assert {r["outcome"] for r in json.loads(out)["results"]} == {"applied"}


def test_two_terminations_in_a_row_still_kill_the_push(tmp_path: Path, fake_gh: FakeGh) -> None:
    target, _ = prepare(tmp_path, fake_gh)
    update(fake_gh, push_sleep=30)
    env = {**os.environ, **GIT_ENV, **fake_gh.env}
    process = subprocess.Popen(
        [str(BOOTSTRAP), "github", str(target)],
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

    process.send_signal(signal.SIGTERM)
    process.send_signal(signal.SIGTERM)  # a supervisor can send it twice
    _, err = process.communicate(timeout=60)

    assert process.returncode == 130, err
    assert "Traceback" not in err, err
    assert gone(fake_gh), "a process of the push is still alive"


def test_a_signal_that_arrives_during_the_kill_is_held_until_the_group_is_dead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_interrupt(signum: int, frame: object) -> None:
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, raise_interrupt)
    process: subprocess.Popen[str] = subprocess.Popen(["sleep", "30"], process_group=0, text=True)
    real_killpg = os.killpg

    def killpg_after_a_signal(pgid: int, sig: int) -> None:
        os.kill(os.getpid(), signal.SIGTERM)  # a second signal, while the cleanup runs
        real_killpg(pgid, sig)

    monkeypatch.setattr(os, "killpg", killpg_after_a_signal)
    try:
        with pytest.raises(KeyboardInterrupt):
            kill_group(process)  # the held signal is raised after the group is dead
    finally:
        signal.signal(signal.SIGTERM, previous)

    assert process.returncode == -signal.SIGKILL

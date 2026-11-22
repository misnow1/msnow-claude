"""Git checks on the target repo. They run git directly, so repo config cannot hide a change."""

import contextlib
import os
import re
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path

from bootstrap.errors import BootstrapError

GIT_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
MAX_TIMEOUT_SECONDS = 3600
DEFAULT_PUSH_TIMEOUT_SECONDS = 300
# These make git print headers and packets, which can hold a token.
TRACE_ENV = re.compile(r"^(GIT_TRACE\w*|GIT_CURL_VERBOSE)$")
PUSH_OK_FLAGS = {"*", "=", " "}  # a new branch, up to date, a fast-forward


def run_git(repo: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args], cwd=repo, env=GIT_ENV, capture_output=True, text=True, check=True
        )
    except subprocess.CalledProcessError as error:
        raise BootstrapError(f"git {args[0]} failed in {repo}: {error.stderr.strip()}") from error
    return result.stdout


def check_repo_root(target: Path) -> None:
    """Raise BootstrapError unless target is the root of a git repo."""
    try:
        top = run_git(target, "rev-parse", "--show-toplevel").strip()
    except BootstrapError as error:
        raise BootstrapError(f"{target} is not in a git repo. Run `git init` first.") from error
    if Path(top).resolve() != target.resolve():
        raise BootstrapError(f"{target} is not the root of its git repo, which is {top}.")


def check_clean_repo_root(target: Path) -> None:
    """Raise BootstrapError unless target is the root of a git repo with no uncommitted change.

    The status call lists untracked files whatever the repo config says. An update can write
    over an untracked file, and git has no copy of it.
    """
    check_repo_root(target)
    status = run_git(
        target, "status", "--porcelain=v1", "--untracked-files=all", "--ignore-submodules=none"
    )
    if status.strip():
        raise BootstrapError(
            f"the worktree of {target} has uncommitted changes. Commit or stash them first:\n"
            + status.rstrip()
        )
    hidden = hidden_paths(target)
    if hidden:
        raise BootstrapError(
            "these files have the skip-worktree or assume-unchanged flag, so git hides their "
            "changes and an update could lose them. Clear the flag with "
            "`git update-index --no-skip-worktree --no-assume-unchanged <file>`:\n  "
            + "\n  ".join(hidden)
        )


def current_branch(target: Path) -> str:
    """Return the branch that HEAD names. It works in a repo with no commit."""
    try:
        # The full ref, because `--short` prints `heads/main` when a tag named `main` exists.
        return run_git(target, "symbolic-ref", "HEAD").strip().removeprefix("refs/heads/")
    except BootstrapError as error:
        raise BootstrapError(f"cannot read the current branch of {target}: {error}") from error


def unmerged_paths(target: Path) -> list[str]:
    """Return the paths with an unresolved merge conflict. The -z flag avoids path quoting."""
    out = run_git(target, "diff", "--name-only", "-z", "--diff-filter=U")
    return [path for path in out.split("\0") if path]


def tracked_paths(target: Path) -> set[str]:
    """Return the paths that git tracks in target."""
    return {path for path in run_git(target, "ls-files", "-z").split("\0") if path}


def hidden_paths(target: Path) -> list[str]:
    """Return the tracked paths whose changes `git status` cannot show.

    `git ls-files -v` marks an assume-unchanged file with a lowercase tag and a skip-worktree
    file with `S`.
    """
    out = run_git(target, "ls-files", "-v", "-z")
    entries = [entry for entry in out.split("\0") if entry]
    return [entry[2:] for entry in entries if entry[0].islower() or entry[0] == "S"]


def ignored_rej_files(target: Path) -> list[str]:
    """Return the ignored `.rej` files. Copier removes a `.rej` file that it did not write."""
    out = run_git(target, "ls-files", "-z", "--others", "--ignored", "--exclude-standard")
    return [path for path in out.split("\0") if path.endswith(".rej")]


def env_seconds(name: str, default: int) -> int:
    """Read a timeout in seconds from an environment variable."""
    raw = os.environ.get(name, str(default))
    if not re.fullmatch(r"[0-9]+", raw) or not 1 <= int(raw) <= MAX_TIMEOUT_SECONDS:
        raise BootstrapError(
            f"{name} must be a whole number of seconds from 1 to {MAX_TIMEOUT_SECONDS}"
        )
    return int(raw)


def push_env() -> dict[str, str]:
    """The environment of `git push`, built when the push starts.

    It drops the variables that make git print headers and packets, which can hold a token. It
    turns trace2 off, because a trace2 target in the config would still write, and the
    environment wins over the config. Git speaks English, so the `fatal:` and `error:` prefixes
    of its messages are the same on every machine.
    """
    env = {k: v for k, v in os.environ.items() if not TRACE_ENV.match(k)}
    env.pop("LC_ALL", None)
    env.pop("LANGUAGE", None)
    env.update(
        GIT_TERMINAL_PROMPT="0",
        GIT_TRACE2="0",
        GIT_TRACE2_EVENT="0",
        GIT_TRACE2_PERF="0",
        LC_MESSAGES="C",
    )
    return env


def push_timeout() -> int:
    """`BOOTSTRAP_PUSH_TIMEOUT` seconds, 300 by default."""
    return env_seconds("BOOTSTRAP_PUSH_TIMEOUT", DEFAULT_PUSH_TIMEOUT_SECONDS)


def rev_parse_branch(target: Path, branch: str) -> str | None:
    """The commit that `refs/heads/BRANCH` points to, or None. Git's messages are not used."""
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}^{{commit}}"],
        cwd=target,
        env=GIT_ENV,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def has_commit(target: Path, branch: str) -> bool:
    return rev_parse_branch(target, branch) is not None


def head_commit(target: Path, branch: str) -> str:
    commit = rev_parse_branch(target, branch)
    if commit is None:
        raise BootstrapError(f"the local branch {branch!r} has no commit")
    return commit


def redact(text: str) -> str:
    """Replace each word that holds a URL or an `@`, which can hold a token."""

    def hide(word: re.Match[str]) -> str:
        found = word.group(0)
        return "<url>" if "://" in found or "@" in found else found

    return re.sub(r"\S+", hide, text)


@dataclass(frozen=True)
class PushResult:
    flag: str  # `*` a new branch, `=` up to date, a space a fast-forward
    exit_code: int


def kill_group(process: "subprocess.Popen[str]") -> None:
    """Kill git and the helpers that it started (git-remote-https, ssh), and reap git.

    A signal that arrives during the cleanup is held until the cleanup is done, so a second
    SIGTERM cannot leave the group alive. Python's Popen does not wait for the child after
    Ctrl-C, so this waits itself. A zombie would stay until the init process reaps it.
    """
    signals = {signal.SIGINT, signal.SIGTERM, signal.SIGHUP}
    held = signal.pthread_sigmask(signal.SIG_BLOCK, signals)
    try:
        # On macOS, a group whose only process has exited but is not reaped gives PermissionError.
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=10)
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, held)


def read_push(out: str, err: str, code: int, refspec: str) -> PushResult:
    """Read `git push --porcelain`: one line for our ref, `FLAG TAB REFSPEC TAB SUMMARY`."""
    lines = [line.split("\t") for line in out.splitlines()]
    ours = next((parts for parts in lines if len(parts) >= 3 and parts[1] == refspec), None)
    if ours is None:
        if code == 0:
            raise BootstrapError("git push gave no result for the branch")
        errors = [e for e in err.splitlines() if re.match(r"(fatal:|error:|remote: error)", e)]
        reason = redact(errors[-1].strip()) if errors else "no reason given"
        raise BootstrapError(f"git push failed: {reason}")
    if ours[0] not in PUSH_OK_FLAGS:
        raise BootstrapError(f"git push failed: {redact(ours[2])}")
    return PushResult(flag=ours[0], exit_code=code)


def push(target: Path, branch: str) -> PushResult:
    """Push the branch to origin, and nothing else.

    The explicit refspec stops `remote.origin.push` and `push.default` from sending it elsewhere.
    The flags stop config from adding a tag, pushing a submodule to its own remote, or signing.
    Git runs in its own process group, so a timeout or Ctrl-C kills the helpers too. This code
    never uses `run_git`, because git can print the URL of the remote, which can hold a token.
    """
    refspec = f"refs/heads/{branch}:refs/heads/{branch}"
    timeout = push_timeout()
    command = [
        "git",
        "-c",
        "advice.pushUpdateRejected=false",
        "push",
        "--porcelain",
        "--set-upstream",
        "--no-follow-tags",
        "--recurse-submodules=no",
        "--no-signed",
        "origin",
        refspec,
    ]
    with subprocess.Popen(
        command,
        cwd=target,
        env=push_env(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
        process_group=0,
    ) as process:
        try:
            out, err = process.communicate(timeout=timeout)
        except BaseException as error:  # a timeout, Ctrl-C, SIGTERM, a hangup
            try:
                kill_group(process)
            except KeyboardInterrupt:
                # A signal came in before the signals were held. The group may still be alive.
                kill_group(process)
            if isinstance(error, subprocess.TimeoutExpired):
                raise BootstrapError(
                    f"git push timed out after {timeout} s; the result is unknown"
                ) from None
            raise
    return read_push(out, err, process.returncode, refspec)

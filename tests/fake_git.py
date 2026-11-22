"""A fake `git` for tests. Every command goes to the real `git`, except `push`.

`push` never reaches GitHub. It is logged, and it goes to a local bare repo (state key `bare`)
through the real `git`, with `url.BARE.pushInsteadOf` set, so the remote name `origin`, its
config and the other options behave as with real git, and the exit codes are git's own.

State switches (the JSON file of the fake `gh`): `push_error` (a dict with `stderr` and `code`
that replace the push), `push_stdout` (a dict with `stdout` or `stdout_latin1`, and `code`),
`push_sleep` (seconds before the push) and `push_sleep_after` (seconds after a push that landed).
"""

import json
import os
import subprocess
import sys
import time
from typing import Any

URL = "https://github.com/octo/widgets.git"
TRACE_KEYS = (
    "GIT_TRACE",
    "GIT_CURL_VERBOSE",
    "GIT_TRACE_REDACT",
    "GIT_TRACE2",
    "GIT_TRACE2_EVENT",
    "GIT_TRACE2_PERF",
    "LC_ALL",
    "LC_MESSAGES",
    "LANGUAGE",
)


def real_git(args: list[str]) -> int:
    real = os.environ["GH_FAKE_REAL_GIT"]
    os.execv(real, [real, *args])
    return 1  # not reached


def subcommand(args: list[str]) -> str:
    """The git subcommand, after any leading `-c name=value` pairs."""
    index = 0
    while index + 1 < len(args) and args[index] == "-c":
        index += 2
    return args[index] if index < len(args) else ""


def log_push(args: list[str]) -> None:
    entry: dict[str, Any] = {"args": args, **{k: os.environ.get(k) for k in TRACE_KEYS}}
    with open(os.environ["GH_FAKE_GIT_LOG"], "a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry) + "\n")
    with open(os.environ["GH_FAKE_PID_FILE"], "w", encoding="utf-8") as stream:
        stream.write(str(os.getpid()))  # with a process group of its own, the PID is the group ID


def push(args: list[str]) -> int:
    with open(os.environ["GH_FAKE_STATE"], encoding="utf-8") as stream:
        state = json.load(stream)
    log_push(args)
    time.sleep(float(state.get("push_sleep", 0)))
    for key in ("push_error", "push_stdout"):
        if key in state:
            replaced = state[key]
            sys.stdout.write(replaced.get("stdout", ""))
            sys.stdout.flush()
            sys.stdout.buffer.write(replaced.get("stdout_latin1", "").encode("latin-1"))
            sys.stderr.write(replaced.get("stderr", ""))
            sys.stdout.flush()
            return int(replaced["code"])
    bare = state.get("bare")
    if not bare:
        print("fake git: a push needs a bare repo in the state", file=sys.stderr)
        return 2
    real = os.environ["GH_FAKE_REAL_GIT"]
    # Only the rewritten local push can run. Any other URL would reach the network.
    allow = ["-c", "protocol.allow=never", "-c", "protocol.file.allow=always"]
    command = [real, *allow, "-c", f"url.{bare}.pushInsteadOf={URL}", *args]
    code = subprocess.run(command, check=False).returncode
    time.sleep(float(state.get("push_sleep_after", 0)))
    return code


def main(args: list[str]) -> int:
    if subcommand(args) == "push":
        return push(args)
    if "push" in args:  # a push in another place of the command line could reach the network
        print(f"fake git: a push that this fake does not parse: {args}", file=sys.stderr)
        return 2
    return real_git(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

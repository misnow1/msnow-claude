"""A fake `gh` for tests. It plays the read side of the GitHub REST API over a JSON state file.

It is strict on purpose: an unknown flag, method or endpoint exits 2, so the adapter cannot
drift from the calls that the tests know. Each call goes to the log file, with the environment
variables that choose the repo and the host.

State keys: `slug` (the one repo that exists), `repo` (the repo object), `labels`, `branches`,
`rulesets` (full detail objects), `org_rulesets` (detail objects that an organization owns), and
the switches `errors` (path -> status and message), `override` (path -> any JSON), `branches_409`,
`hide_bypass`, `old_gh`, `garbage`, `plain_error` and `leak_org`.
"""

import json
import os
import re
import sys
from typing import Any, NoReturn

LIST_KEYS = ("id", "name", "target", "source_type", "source", "enforcement", "node_id", "_links")
ALLOWED_FLAGS = {"--paginate", "--slurp"}


def fail(status: int, message: str, paginate: bool) -> NoReturn:
    """Print an error as `gh api` does: JSON on stdout, one `gh:` line on stderr, exit 1."""
    body = {
        "message": message,
        "documentation_url": "https://docs.github.com/rest",
        "status": str(status),
    }
    print(json.dumps([body] if paginate else body))
    print(f"gh: {message} (HTTP {status})", file=sys.stderr)
    sys.exit(1)


def unsupported(what: str) -> NoReturn:
    print(f"fake gh: unsupported {what}", file=sys.stderr)
    sys.exit(2)


def summary(detail: dict[str, Any]) -> dict[str, Any]:
    """The list endpoint gives no `conditions`, `rules` or `bypass_actors`."""
    return {key: detail[key] for key in LIST_KEYS if key in detail}


def answer(state: dict[str, Any], path: str, query: str, paginate: bool, slurp: bool) -> Any:
    found = re.fullmatch(r"repos/([^/]+)/([^/]+)(?:/(.*))?", path)
    if not found or f"{found[1]}/{found[2]}" != state["slug"]:
        fail(404, "Not Found", paginate)
    rest = found[3]
    if rest is None and not query:
        return state["repo"]
    if rest == "labels" and query == "per_page=100" and paginate and slurp:
        labels = state["labels"]
        return [labels[i : i + 100] for i in range(0, len(labels), 100)] or [[]]
    if rest == "branches" and query == "per_page=1" and not paginate:
        if state.get("branches_409"):
            fail(409, "Git Repository is empty.", paginate)
        return state["branches"][:1]
    return answer_rulesets(state, rest or "", query, paginate, slurp)


def answer_rulesets(
    state: dict[str, Any], rest: str, query: str, paginate: bool, slurp: bool
) -> Any:
    if rest == "rulesets" and query in ("includes_parents=false", "") and paginate and slurp:
        rulesets = list(state.get("rulesets", []))
        # GitHub lists the rulesets of the organization too, unless the request asks for no parents.
        if query == "" or state.get("leak_org"):
            rulesets += state.get("org_rulesets", [])
        return [[summary(r) for r in rulesets]]
    detail = re.fullmatch(r"rulesets/(\d+)", rest)
    if detail and not query and not paginate:
        for ruleset in state.get("rulesets", []) + state.get("org_rulesets", []):
            if ruleset["id"] == int(detail[1]):
                hidden = state.get("hide_bypass")
                return {k: v for k, v in ruleset.items() if not (hidden and k == "bypass_actors")}
        fail(404, "Not Found", paginate)
    unsupported(f"endpoint .../{rest}?{query}")


def main(argv: list[str]) -> None:
    with open(os.environ["GH_FAKE_STATE"], encoding="utf-8") as stream:
        state = json.load(stream)
    log = {
        "args": argv,
        "GH_REPO": os.environ.get("GH_REPO"),
        "GH_HOST": os.environ.get("GH_HOST"),
        "GH_DEBUG": os.environ.get("GH_DEBUG"),
        "DEBUG": os.environ.get("DEBUG"),
        "GODEBUG": os.environ.get("GODEBUG"),
        "GH_FORCE_TTY": os.environ.get("GH_FORCE_TTY"),
        "CLICOLOR_FORCE": os.environ.get("CLICOLOR_FORCE"),
    }
    with open(os.environ["GH_FAKE_LOG"], "a", encoding="utf-8") as stream:
        stream.write(json.dumps(log) + "\n")
    if len(argv) < 2 or argv[0] != "api":
        unsupported(f"command {argv[:1]}")
    flags = argv[2:]
    if set(flags) - ALLOWED_FLAGS:
        unsupported(f"flags {flags}")
    paginate, slurp = "--paginate" in flags, "--slurp" in flags
    if slurp and state.get("old_gh"):
        print("unknown flag: --slurp", file=sys.stderr)
        sys.exit(1)
    if state.get("plain_error"):  # an error with no `gh:` line, and a line that must not show
        print("debug: Authorization: token SECRET-HEADER", file=sys.stderr)
        print("the real failure", file=sys.stderr)
        sys.exit(1)
    if state.get("garbage"):
        print("this is not json")
        return
    path, _, query = argv[1].partition("?")
    if path in state.get("override", {}):  # an answer of the wrong shape
        print(json.dumps(state["override"][path]))
        return
    injected = state.get("errors", {}).get(path)
    if injected:
        fail(injected["status"], injected["message"], paginate)
    print(json.dumps(answer(state, path, query, paginate, slurp)))


if __name__ == "__main__":
    main(sys.argv[1:])

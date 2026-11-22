"""A fake `gh` for tests. It plays the GitHub REST API over a JSON state file.

It is strict on purpose: an unknown flag, method or endpoint exits 2, so the adapter cannot
drift from the calls that the tests know. A write changes the state file, so a second run reads
what the first one wrote. Each call goes to the log file with its method, body and the
environment variables that choose the repo and the host.

State keys: `slug` (the one repo that exists), `repo` (the repo object), `labels`, `branches`,
`rulesets` (full detail objects), `org_rulesets` (detail objects that an organization owns).
With `bare` (a local bare repo that the fake `git` pushes to), the branches are the branches of
that repo, and `default_after_first_push` is the default branch that the repo reports once it
has a branch.
Switches: `errors` (`"METHOD path"`, or `"path"` for a GET, to status and message), `override`
(path to any JSON), `branches_409`, `hide_bypass`, `old_gh`, `garbage`, `plain_error`,
`leak_org`, `ignore_fields` (a PATCH leaves these fields alone), `drop_on_write` (a rule type
that a ruleset write loses), `touch_after_first_detail_read`, `sleep` (seconds before a write)
`garbage_on_write` (the write applies and the answer is not JSON), `empty_on_write` (the write
applies and the answer is empty), `response_as` (the write response is this JSON),
`response_without` (keys that a write response lacks) and `reread_override` (path to the answer
of the second and later reads of that path). An injected error can have `after`: the number
of calls to let pass.
"""

import copy
import json
import os
import re
import subprocess
import sys
import time
from typing import Any, NoReturn

LIST_KEYS = ("id", "name", "target", "source_type", "source", "enforcement", "node_id", "_links")
REPO_FIELDS = {
    "allow_squash_merge",
    "allow_merge_commit",
    "allow_rebase_merge",
    "squash_merge_commit_title",
    "squash_merge_commit_message",
    "delete_branch_on_merge",
    "allow_auto_merge",
}
LABEL_KEYS = {"name", "color", "description"}
RULESET_KEYS = {"name", "target", "enforcement", "conditions", "rules", "bypass_actors"}
RULE_TYPES = {
    "deletion",
    "non_fast_forward",
    "pull_request",
    "required_status_checks",
    "required_signatures",
}
PULL_REQUEST_KEYS = {
    "required_approving_review_count",
    "dismiss_stale_reviews_on_push",
    "require_code_owner_review",
    "require_last_push_approval",
    "required_review_thread_resolution",
    "allowed_merge_methods",
}
REPO_PATH = re.compile(r"repos/([^/]+)/([^/]+)(?:/(.*))?")


def fail(
    status: int, message: str, paginate: bool = False, errors: list[Any] | None = None
) -> NoReturn:
    """Print an error as `gh api` does: JSON on stdout, one `gh:` line on stderr, exit 1."""
    body: dict[str, Any] = {
        "message": message,
        "documentation_url": "https://docs.github.com/rest",
        "status": str(status),
    }
    if errors:
        body["errors"] = errors
    print(json.dumps([body] if paginate else body))
    print(f"gh: {message} (HTTP {status})", file=sys.stderr)
    sys.exit(1)


def unsupported(what: str) -> NoReturn:
    print(f"fake gh: unsupported {what}", file=sys.stderr)
    sys.exit(2)


def summary(detail: dict[str, Any]) -> dict[str, Any]:
    """The list endpoint gives no `conditions`, `rules` or `bypass_actors`."""
    return {key: detail[key] for key in LIST_KEYS if key in detail}


def split_repo_path(state: dict[str, Any], path: str, paginate: bool = False) -> str | None:
    found = REPO_PATH.fullmatch(path)
    if not found or f"{found[1]}/{found[2]}" != state["slug"]:
        fail(404, "Not Found", paginate)
    return found[3]


def answer(state: dict[str, Any], path: str, query: str, paginate: bool, slurp: bool) -> Any:
    rest = split_repo_path(state, path, paginate)
    if rest is None and not query:
        return repo_object(state)
    if rest == "labels" and query == "per_page=100" and paginate and slurp:
        labels = state["labels"]
        return [labels[i : i + 100] for i in range(0, len(labels), 100)] or [[]]
    if rest == "branches" and query == "per_page=1" and not paginate:
        if state.get("branches_409"):
            fail(409, "Git Repository is empty.", paginate)
        return branches(state)[:1]
    return answer_rulesets(state, rest or "", query, paginate, slurp)


def branches(state: dict[str, Any]) -> list[dict[str, str]]:
    """The branches of the bare repo when the state has one, else the listed branches."""
    bare = state.get("bare")
    if not bare:
        found: list[dict[str, str]] = state["branches"]
        return found
    command = [os.environ["GH_FAKE_REAL_GIT"], "--git-dir", bare, "for-each-ref"]
    out = subprocess.run(
        [*command, "--format=%(refname:short)", "refs/heads"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [{"name": name} for name in out.split()]


def repo_object(state: dict[str, Any]) -> dict[str, Any]:
    repo: dict[str, Any] = copy.deepcopy(state["repo"])
    if state.get("default_after_first_push") and branches(state):
        repo["default_branch"] = state["default_after_first_push"]  # someone else pushed first
    return repo


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
                return read_detail(state, ruleset)
        fail(404, "Not Found", paginate)
    unsupported(f"endpoint .../{rest}?{query}")


def read_detail(state: dict[str, Any], ruleset: dict[str, Any]) -> dict[str, Any]:
    hidden = state.get("hide_bypass")
    shown = {k: v for k, v in ruleset.items() if not (hidden and k == "bypass_actors")}
    if state.get("touch_after_first_detail_read") and not state.get("touched"):
        state["touched"] = True  # someone edits the ruleset after this read
        ruleset["updated_at"] = tick(state)
    return shown


def tick(state: dict[str, Any]) -> str:
    state["clock"] = state.get("clock", 0) + 1
    return f"2026-11-21T00:00:{state['clock']:02d}Z"


def invalid_ruleset(body: Any) -> list[str] | None:
    """The reasons that GitHub gives a 422 for a ruleset body, or None when the body is fine."""
    if not isinstance(body, dict) or set(body) - RULESET_KEYS:
        return [f"Invalid keys: {sorted(set(body) - RULESET_KEYS)}"]
    if "name" not in body or "enforcement" not in body:
        return ["name and enforcement are required"]
    for rule in body.get("rules", []):
        kind = rule.get("type")
        if kind not in RULE_TYPES:
            return [f"Invalid rule type {kind}"]
        if "parameters" in rule and kind in {"deletion", "non_fast_forward"}:
            return [f"The rule {kind} takes no parameters"]
        if kind == "pull_request" and not set(rule.get("parameters", {})) >= PULL_REQUEST_KEYS:
            return ["Missing required pull_request parameters"]
    return invalid_shapes(body)


def invalid_shapes(body: dict[str, Any]) -> list[str] | None:
    """Check the shapes that GitHub checks inside a ruleset: conditions, actors, status checks."""
    ref_name = body.get("conditions", {}).get("ref_name", {})
    if body.get("conditions") and not (
        isinstance(ref_name.get("include"), list) and isinstance(ref_name.get("exclude"), list)
    ):
        return ["conditions.ref_name needs include and exclude lists"]
    for actor in body.get("bypass_actors", []):
        if not {"actor_type", "bypass_mode"} <= set(actor) or actor["bypass_mode"] not in {
            "always",
            "pull_request",
        }:
            return [f"Invalid bypass actor {actor}"]
    for rule in body.get("rules", []):
        parameters = rule.get("parameters", {})
        if rule["type"] == "required_status_checks" and not {
            "required_status_checks",
            "strict_required_status_checks_policy",
        } <= set(parameters):
            return ["Missing required status check parameters"]
    return None


def serverize(
    state: dict[str, Any], body: dict[str, Any], rid: int, old: dict[str, Any] | None
) -> dict[str, Any]:
    """Add what the server adds to a ruleset, as the real response has it."""
    ruleset = copy.deepcopy(body)
    ruleset.setdefault("target", "branch")
    ruleset.setdefault("conditions", {})
    ruleset.setdefault("bypass_actors", [])
    for rule in ruleset.get("rules", []):
        if rule["type"] == "pull_request":
            rule["parameters"].setdefault("required_reviewers", [])
            rule["parameters"].setdefault("require_extra_approval_for_unattributed_changes", True)
    dropped = state.get("drop_on_write")
    ruleset["rules"] = [r for r in ruleset.get("rules", []) if r["type"] != dropped]
    stamp = tick(state)
    href = f"https://api.github.com/repos/{state['slug']}/rulesets/{rid}"
    return {
        **ruleset,
        "id": rid,
        "source_type": "Repository",
        "source": state["slug"],
        "node_id": f"RRS_{rid}",
        "created_at": old["created_at"] if old else stamp,
        "updated_at": stamp,
        "current_user_can_bypass": "pull_requests_only",
        "_links": {"self": {"href": href}},
    }


def write(state: dict[str, Any], method: str, path: str, body: Any) -> Any:
    rest = split_repo_path(state, path)
    if method == "PATCH" and rest is None:
        return patch_repo(state, body)
    if method == "POST" and rest == "labels":
        return post_label(state, body)
    if method == "POST" and rest == "rulesets":
        return post_ruleset(state, body)
    put = re.fullmatch(r"rulesets/(\d+)", rest or "")
    if method == "PUT" and put:
        return put_ruleset(state, int(put[1]), body)
    unsupported(f"write {method} {path}")


def patch_repo(state: dict[str, Any], body: dict[str, Any]) -> Any:
    ignored = set(state.get("ignore_fields", []))
    for key, value in body.items():  # GitHub ignores a key that it does not know
        if key in REPO_FIELDS and key not in ignored:
            state["repo"][key] = value
    return state["repo"]


def post_label(state: dict[str, Any], body: dict[str, Any]) -> Any:
    if set(body) - LABEL_KEYS or "name" not in body:
        fail(422, "Validation Failed", errors=[{"resource": "Label", "code": "invalid"}])
    if body["name"].lower() in {label["name"].lower() for label in state["labels"]}:
        errors = [{"resource": "Label", "code": "already_exists", "field": "name"}]
        fail(422, "Validation Failed", errors=errors)
    label = {"id": len(state["labels"]) + 1, "default": False, **body}
    state["labels"].append(label)
    return label


def post_ruleset(state: dict[str, Any], body: Any) -> Any:
    reasons = invalid_ruleset(body)
    if reasons:
        fail(422, "Invalid request.", errors=reasons)
    if body["name"] in {r["name"] for r in state.get("rulesets", [])}:
        fail(422, "Validation Failed", errors=["Name must be unique"])
    rid = max([r["id"] for r in state.get("rulesets", [])] + [1000]) + 1
    ruleset = serverize(state, body, rid, None)
    state.setdefault("rulesets", []).append(ruleset)
    return ruleset


def put_ruleset(state: dict[str, Any], rid: int, body: Any) -> Any:
    reasons = invalid_ruleset(body)
    if reasons:
        fail(422, "Invalid request.", errors=reasons)
    for index, old in enumerate(state.get("rulesets", [])):
        if old["id"] == rid:
            state["rulesets"][index] = serverize(state, body, rid, old)
            return state["rulesets"][index]
    fail(404, "Not Found")


def parse(argv: list[str]) -> dict[str, Any]:
    if len(argv) < 2 or argv[0] != "api":
        unsupported(f"command {argv[:1]}")
    call: dict[str, Any] = {
        "endpoint": argv[1],
        "method": "GET",
        "paginate": False,
        "slurp": False,
        "input": False,
    }
    rest = iter(argv[2:])
    for flag in rest:
        if flag == "--paginate":
            call["paginate"] = True
        elif flag == "--slurp":
            call["slurp"] = True
        elif flag == "--method" and (method := next(rest, "")) in {"GET", "PATCH", "POST", "PUT"}:
            call["method"] = method
        elif flag == "--input" and next(rest, "") == "-":
            call["input"] = True
        else:
            unsupported(f"flags {argv[2:]}")
    # `gh api` sends a POST when it has a body and no method, so a write names both.
    if call["input"] != (call["method"] != "GET"):
        unsupported(f"a body and a method that do not go together: {argv[2:]}")
    return call


def log_call(argv: list[str], call: dict[str, Any], body: Any) -> None:
    keys = ("GH_REPO", "GH_HOST", "GH_DEBUG", "DEBUG", "GODEBUG", "GH_FORCE_TTY", "CLICOLOR_FORCE")
    env = {k: os.environ.get(k) for k in keys}
    entry = {"args": argv, "method": call["method"], "body": body, **env}
    with open(os.environ["GH_FAKE_LOG"], "a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry) + "\n")


def switches(state: dict[str, Any], call: dict[str, Any], path: str) -> bool:
    """Handle the failure switches. Return True when the call is finished."""
    if call["slurp"] and state.get("old_gh"):
        print("unknown flag: --slurp", file=sys.stderr)
        sys.exit(1)
    if state.get("plain_error"):  # an error with no `gh:` line, and a line that must not show
        print("debug: Authorization: token SECRET-HEADER", file=sys.stderr)
        print("the real failure", file=sys.stderr)
        sys.exit(1)
    if state.get("garbage") and call["method"] == "GET":
        print("this is not json")
        return True
    if path in state.get("override", {}):  # an answer of the wrong shape
        print(json.dumps(state["override"][path]))
        return True
    reread = state.get("reread_override", {})
    if path in reread and call["method"] == "GET":
        key = f"reread {path}"
        seen = state.setdefault("counters", {}).get(key, 0)
        state["counters"][key] = seen + 1
        save(state)
        if seen >= 1:  # the first read is the plan, the later ones are the check before a PUT
            print(json.dumps(reread[path]))
            return True
    errors = state.get("errors", {})
    injected = errors.get(f"{call['method']} {path}")
    if injected is None and call["method"] == "GET":
        injected = errors.get(path)
    if injected and fires(state, call, path, injected):
        fail(injected["status"], injected["message"], call["paginate"], injected.get("errors"))
    return False


def fires(state: dict[str, Any], call: dict[str, Any], path: str, injected: dict[str, Any]) -> bool:
    """An injected error fires after `after` calls have passed. The count is kept in the state."""
    key = f"{call['method']} {path}"
    seen = state.setdefault("counters", {}).get(key, 0)
    state["counters"][key] = seen + 1
    save(state)
    return bool(seen >= injected.get("after", 0))


def save(state: dict[str, Any]) -> None:
    with open(os.environ["GH_FAKE_STATE"], "w", encoding="utf-8") as stream:
        json.dump(state, stream)


def print_write_answer(state: dict[str, Any], result: dict[str, Any]) -> None:
    if state.get("garbage_on_write"):
        print("ok")
    elif state.get("empty_on_write"):
        return
    elif "response_as" in state:
        print(json.dumps(state["response_as"]))
    else:
        hidden = state.get("response_without", [])
        print(json.dumps({k: v for k, v in result.items() if k not in hidden}))


def main(argv: list[str]) -> None:
    with open(os.environ["GH_FAKE_STATE"], encoding="utf-8") as stream:
        state = json.load(stream)
    call = parse(argv)
    body = json.loads(sys.stdin.read()) if call["input"] else None
    log_call(argv, call, body)
    path, _, query = call["endpoint"].partition("?")
    if switches(state, call, path):
        return
    if call["method"] == "GET":
        print(json.dumps(answer(state, path, query, call["paginate"], call["slurp"])))
    else:
        time.sleep(float(state.get("sleep", 0)))
        result = write(state, call["method"], path, body)
        save(state)
        print_write_answer(state, result)
        return
    save(state)


if __name__ == "__main__":
    main(sys.argv[1:])

"""Build the ruleset body that bootstrap wants, on top of the ruleset that exists.

Bootstrap owns a short list of values. A merge sets them and keeps all else, so a second run
after an applied plan gives the same body and the plan is empty.
"""

import copy
import re
from collections.abc import Iterator
from fnmatch import fnmatchcase
from itertools import islice
from typing import Any

from bootstrap.errors import BootstrapError
from bootstrap.github.models import CurrentRule, CurrentRuleset, DesiredState, RulesetBody

ADMIN_ROLE_ID = 5
ACTIONS_APP_ID = 15368  # GitHub Actions. Any commit status can use a name without the id.
WHOLE_REPO_PATTERNS = {"~DEFAULT_BRANCH", "~ALL"}
MAX_BRACE_OPTIONS = 1000

Rule = dict[str, Any]


def rule_dict(rule: CurrentRule) -> Rule:
    """A rule as the request body has it. A rule without parameters has no `parameters` key."""
    return {"type": rule.type, **({"parameters": rule.parameters} if rule.parameters else {})}


def body_of(ruleset: CurrentRuleset) -> RulesetBody:
    """The existing ruleset in the shape of the request body."""
    return RulesetBody(
        name=ruleset.name,
        target=ruleset.target or "",
        enforcement=ruleset.enforcement or "",
        conditions=ruleset.conditions or {},
        rules=[rule_dict(rule) for rule in ruleset.rules],
        bypass_actors=ruleset.bypass_actors,
    )


def expand_braces(pattern: str) -> Iterator[str]:
    """Expand `{a,b}`, which `fnmatchcase` does not know. A generator, so `any` can stop early."""
    found = re.search(r"\{([^{}]*)\}", pattern)
    if found is None:
        yield pattern
        return
    head, tail = pattern[: found.start()], pattern[found.end() :]
    for option in found.group(1).split(","):
        yield from expand_braces(head + option + tail)


def hits_default_branch(pattern: str, default_branch: str | None) -> bool:
    if pattern in WHOLE_REPO_PATTERNS:
        return True
    if default_branch is None:
        raise BootstrapError(
            f"the ruleset excludes {pattern!r}. The state needs repository.default_branch, "
            "so the command can check whether the pattern hides the default branch."
        )
    ref = f"refs/heads/{default_branch}"
    # GitHub reads `[^x]` as a negated class, and Python reads `[!x]`. The match can still
    # differ from GitHub in rare cases. Removing an exclude that GitHub would not match is safe.
    options = list(islice(expand_braces(pattern), MAX_BRACE_OPTIONS + 1))
    if len(options) > MAX_BRACE_OPTIONS:
        raise BootstrapError(
            f"the ruleset excludes {pattern!r}, which has too many brace options "
            f"(more than {MAX_BRACE_OPTIONS}). Simplify the pattern."
        )
    return any(fnmatchcase(ref, option.replace("[^", "[!")) for option in options)


def merge_conditions(conditions: dict[str, Any], default_branch: str | None) -> dict[str, Any]:
    merged = copy.deepcopy(conditions)
    ref_name = merged.setdefault("ref_name", {})
    include = list(ref_name.get("include", []))
    if "~DEFAULT_BRANCH" not in include:
        include.append("~DEFAULT_BRANCH")
    ref_name["include"] = include
    ref_name["exclude"] = [
        pattern
        for pattern in ref_name.get("exclude", [])
        if not hits_default_branch(pattern, default_branch)
    ]
    return merged


def find(rules: list[Rule], kind: str) -> Rule | None:
    return next((rule for rule in rules if rule["type"] == kind), None)


def merge_pull_request(rules: list[Rule], desired: DesiredState) -> None:
    wanted = desired.ruleset
    rule = find(rules, "pull_request")
    if rule is None:
        # GitHub requires these three parameters, so a new rule sets them to their default.
        rule = {
            "type": "pull_request",
            "parameters": {
                "require_code_owner_review": False,
                "require_last_push_approval": False,
                "required_review_thread_resolution": False,
            },
        }
        rules.append(rule)
    parameters = rule.setdefault("parameters", {})
    # The count is a minimum, so a stricter value of the user stays.
    parameters["required_approving_review_count"] = max(
        parameters.get("required_approving_review_count") or 0,
        wanted.required_approving_review_count,
    )
    parameters["dismiss_stale_reviews_on_push"] = wanted.dismiss_stale_reviews_on_push
    if set(parameters.get("allowed_merge_methods", [])) != set(wanted.allowed_merge_methods):
        parameters["allowed_merge_methods"] = list(wanted.allowed_merge_methods)


def merge_checks(rules: list[Rule], desired: DesiredState) -> None:
    if not desired.required_checks:
        return
    rule = find(rules, "required_status_checks")
    if rule is None:
        rule = {
            "type": "required_status_checks",
            "parameters": {
                "required_status_checks": [],
                "strict_required_status_checks_policy": False,
            },
        }
        rules.append(rule)
    checks: list[dict[str, Any]] = rule.setdefault("parameters", {}).setdefault(
        "required_status_checks", []
    )
    for context in desired.required_checks:
        entry = next((check for check in checks if check.get("context") == context), None)
        if entry is None:
            entry = {"context": context}
            checks.append(entry)
        entry["integration_id"] = ACTIONS_APP_ID


def merge_rules(current: list[Rule], desired: DesiredState) -> list[Rule]:
    rules = copy.deepcopy(current)
    for kind in ("deletion", "non_fast_forward"):
        if find(rules, kind) is None:
            rules.append({"type": kind})
    merge_pull_request(rules, desired)
    merge_checks(rules, desired)
    return rules


def merge_bypass(actors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = copy.deepcopy(actors)
    admins = [
        actor
        for actor in merged
        if actor.get("actor_type") == "RepositoryRole" and actor.get("actor_id") == ADMIN_ROLE_ID
    ]
    for admin in admins:
        admin["bypass_mode"] = "pull_request"
    if not admins:
        merged.append(
            {
                "actor_id": ADMIN_ROLE_ID,
                "actor_type": "RepositoryRole",
                "bypass_mode": "pull_request",
            }
        )
    return merged


def wanted_body(
    desired: DesiredState, existing: CurrentRuleset | None, default_branch: str | None
) -> RulesetBody:
    """The body to send: what bootstrap owns, set on top of the ruleset that exists (if any)."""
    current = body_of(existing) if existing else None
    return RulesetBody(
        name=desired.ruleset.name,
        target="branch",
        enforcement="active",
        conditions=merge_conditions(current.conditions if current else {}, default_branch),
        rules=merge_rules(current.rules if current else [], desired),
        bypass_actors=merge_bypass(current.bypass_actors if current else []),
    )

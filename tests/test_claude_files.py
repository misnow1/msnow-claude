"""The Claude Code files of a bootstrapped repo: settings, agents and the severity policy doc."""

import json
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml
from conftest import FixtureTemplate, commit_all, make_repo

Apply = Callable[..., subprocess.CompletedProcess[str]]

VARIABLE = "CLAUDE_CODE_SUBAGENT_MODEL"
FORCE = "CLAUDE_CODE_SUBAGENT_MODEL_FORCE"
AGENTS = ["challenger", "issue-reviewer"]
TOOLS = ["Read", "Grep", "Glob", "Bash", "WebFetch", "WebSearch"]
MAJORS = ["unauthorized access", "data loss or deletion", "exposed secrets"]


def applied(tmp_path: Path, released: FixtureTemplate, run_apply: Apply) -> Path:
    target = make_repo(tmp_path / "repo")
    result = run_apply(target, released.path)
    assert result.returncode == 0, result.stderr
    return target


def settings_of(target: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((target / ".claude" / "settings.json").read_text())
    return data


def frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """The YAML frontmatter of an agent file and its body."""
    _, head, body = text.split("---\n", 2)
    data: dict[str, Any] = yaml.safe_load(head)
    return data, body


def subagent_model_problems(settings: Mapping[str, Any]) -> list[str]:
    """What keeps a sub-agent with no model from running on Opus, without forcing every model."""
    env = settings.get("env") or {}
    problems = []
    if VARIABLE not in env:
        problems.append(f"env.{VARIABLE} is missing")
    elif env[VARIABLE] != "opus":
        problems.append(f"env.{VARIABLE} is {env[VARIABLE]!r}, not the alias 'opus'")
    if FORCE in env:
        problems.append(f"env.{FORCE} is set")
    return problems


def test_the_settings_use_opusplan_turn_off_auto_memory_and_know_the_plugin(
    tmp_path: Path, released: FixtureTemplate, run_apply: Apply
) -> None:
    settings = settings_of(applied(tmp_path, released, run_apply))

    assert settings["model"] == "opusplan"
    assert settings["autoMemoryEnabled"] is False
    assert settings["extraKnownMarketplaces"] == {
        "mattpocock": {"source": {"source": "github", "repo": "mattpocock/skills"}}
    }
    assert settings["enabledPlugins"] == {"mattpocock-skills@mattpocock": True}
    assert subagent_model_problems(settings) == []


@pytest.mark.parametrize(
    ("env", "problem"),
    [
        ({}, f"env.{VARIABLE} is missing"),
        ({VARIABLE: "sonnet"}, f"env.{VARIABLE} is 'sonnet', not the alias 'opus'"),
        (
            {VARIABLE: "claude-opus-5-5"},
            f"env.{VARIABLE} is 'claude-opus-5-5', not the alias 'opus'",
        ),
        ({VARIABLE: "opus", FORCE: "1"}, f"env.{FORCE} is set"),
    ],
    ids=["missing", "another model", "a full model id", "forced"],
)
def test_the_settings_check_reports_each_problem_of_the_subagent_model(
    env: dict[str, str], problem: str
) -> None:
    assert subagent_model_problems({"env": env}) == [problem]


@pytest.mark.parametrize("name", AGENTS)
def test_each_agent_runs_on_opus_with_a_fixed_list_of_tools(
    tmp_path: Path, released: FixtureTemplate, run_apply: Apply, name: str
) -> None:
    target = applied(tmp_path, released, run_apply)

    meta, body = frontmatter((target / ".claude" / "agents" / f"{name}.md").read_text())

    assert meta["name"] == name
    assert meta["description"].strip()
    assert meta["model"] == "opus"
    # A missing `tools` field gives the agent every tool, edit tools included.
    assert "tools" in meta
    assert [tool.strip() for tool in meta["tools"].split(",")] == TOOLS
    assert "docs/severity-policy.md" in body
    for severity in ("major", "minor", "nit"):
        assert f"`{severity}`" in body


def test_the_severity_policy_names_the_default_majors(
    tmp_path: Path, released: FixtureTemplate, run_apply: Apply
) -> None:
    text = (applied(tmp_path, released, run_apply) / "docs" / "severity-policy.md").read_text()

    for major in MAJORS:
        assert major in text.lower()


def test_an_update_keeps_the_severity_policy_that_the_project_owns(
    tmp_path: Path, template: FixtureTemplate, run_apply: Apply
) -> None:
    template.release("v0.1.0")
    target = make_repo(tmp_path / "repo")
    assert run_apply(target, template.path).returncode == 0
    mine = "# Severity policy\n\nOur own classes of a major.\n"
    (target / "docs" / "severity-policy.md").write_text(mine)
    commit_all(target)
    doc = template.path / "template" / "docs" / "severity-policy.md"
    # The new version changes the doc too. Without the skip list, the update merges that change.
    template.release("v0.2.0", {"template/docs/severity-policy.md": doc.read_text() + "\nNew.\n"})

    result = run_apply(target, template.path)

    assert result.returncode == 0, result.stderr
    assert (target / "docs" / "severity-policy.md").read_text() == mine


def test_an_update_does_not_restore_a_setting_that_the_user_deleted(
    tmp_path: Path, template: FixtureTemplate, run_apply: Apply
) -> None:
    template.release("v0.1.0")
    target = make_repo(tmp_path / "repo")
    assert run_apply(target, template.path).returncode == 0
    path = target / ".claude" / "settings.json"
    settings = json.loads(path.read_text())
    del settings["env"][VARIABLE]
    path.write_text(json.dumps(settings, indent=2) + "\n")
    commit_all(target)
    # The new version adds a key, as a later version adds `hooks`.
    source = json.loads((template.path / "template" / ".claude" / "settings.json").read_text())
    source["hooks"] = {}
    template.release(
        "v0.2.0", {"template/.claude/settings.json": json.dumps(source, indent=2) + "\n"}
    )

    result = run_apply(target, template.path)

    assert result.returncode == 0, result.stderr
    updated = settings_of(target)
    assert VARIABLE not in updated["env"]
    assert updated["hooks"] == {}

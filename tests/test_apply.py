"""Tests run the `bootstrap` command as a subprocess against a temporary repo.

Each test applies a fixture template tagged `v0.1.0`, not the live tags of this repo.
"""

import subprocess
from collections.abc import Callable
from pathlib import Path

from conftest import FixtureTemplate, commit_all, make_repo

Apply = Callable[..., subprocess.CompletedProcess[str]]


def tree(path: Path) -> set[str]:
    return {
        str(p.relative_to(path))
        for p in path.rglob("*")
        if p.is_file() and ".git" not in p.relative_to(path).parts
    }


def test_apply_to_empty_repo_writes_agent_docs_and_answers_file(
    tmp_path: Path, released: FixtureTemplate, run_apply: Apply
) -> None:
    target = make_repo(tmp_path / "repo")

    result = run_apply(target, released.path)

    assert result.returncode == 0, result.stderr
    assert tree(target) == {
        ".claude/agents/challenger.md",
        ".claude/agents/issue-reviewer.md",
        ".claude/settings.json",
        "AGENTS.md",
        "CLAUDE.md",
        "docs/severity-policy.md",
        ".copier-answers.yml",
        ".github/bootstrap.yml",
        ".github/workflows/shared.yml",
        ".pre-commit-config.yaml",
        "Makefile",
        "scripts/check-file-length",
    }


AGENTS_SECTIONS: dict[str, tuple[str, ...]] = {
    "tdd": ("Write a failing test first",),
    "libraries": ("Prefer a maintained third-party library",),
    "scripts": ("Each repeatable process has a committed script",),
    "issues": (
        "Before you file or edit an issue or a batch of issues, search for related issues",
        "Then have an independent reviewer review them",
    ),
    "memory": ("Decisions go in `docs/adr/`",),
    "checks": ("Run `make check` before each commit",),
    "challenge": (
        "A round is one review and one fix pass",
        "fix each finding, majors included",
        "after three rounds",
        "With a major left, stop and ask the user",
        "the work continues",
        "when a fix would change the approved plan, stop and ask the user",
        "not your conclusions",
        "The reviewer does not edit files",
        "`docs/severity-policy.md`",
        "`Plan review`",
        "`No remaining findings.`",
        "before any footer",
    ),
}
CLAUDE_SECTIONS: dict[str, tuple[str, ...]] = {
    "workflow": (
        "Start each change in plan mode",
        "Implement only after the user approves",
        "Run the `challenger` agent on each plan before you present it",
        "and on each diff before a pull request",
        "without a `model` parameter",
        "Run the `issue-reviewer` agent",
        "they are not read-only by tool",
        "`/mattpocock-skills:pr`",
    ),
    "subagent-model": (
        "`CLAUDE_CODE_SUBAGENT_MODEL`",
        "does not set `CLAUDE_CODE_SUBAGENT_MODEL_FORCE`",
        "still wins",
        "Explore and Plan",
        "costs more",
        "trust the folder",
        "subagents/agent-",
    ),
}


def section(text: str, name: str) -> str:
    """The text of a marked section, with each run of whitespace as one space (lines wrap)."""
    begin = text.index(f"<!-- bootstrap:begin {name} -->")
    end = text.index(f"<!-- bootstrap:end {name} -->")
    assert begin < end
    return " ".join(text[begin:end].split())


def test_agents_md_has_marked_sections_that_state_each_rule(
    tmp_path: Path, released: FixtureTemplate, run_apply: Apply
) -> None:
    target = make_repo(tmp_path / "repo")

    result = run_apply(target, released.path)

    assert result.returncode == 0, result.stderr
    text = (target / "AGENTS.md").read_text()
    for name, phrases in AGENTS_SECTIONS.items():
        for phrase in phrases:
            assert phrase in section(text, name), (name, phrase)
    assert "Domain terms go in `GLOSSARY.md`" in text
    assert "Rules for how to work go in `AGENTS.md`" in text


def test_claude_md_imports_agents_md_in_a_marked_section(
    tmp_path: Path, released: FixtureTemplate, run_apply: Apply
) -> None:
    target = make_repo(tmp_path / "repo")

    result = run_apply(target, released.path)

    assert result.returncode == 0, result.stderr
    text = (target / "CLAUDE.md").read_text()
    begin = text.index("<!-- bootstrap:begin agents-import -->")
    end = text.index("<!-- bootstrap:end agents-import -->")
    assert "@AGENTS.md" in text[begin:end].splitlines()


def test_missing_target_fails_and_is_not_created(
    tmp_path: Path, released: FixtureTemplate, run_apply: Apply
) -> None:
    target = tmp_path / "missing"

    result = run_apply(target, released.path)

    assert result.returncode != 0
    assert not target.exists()


def test_existing_agent_files_keep_user_text_and_get_the_sections(
    tmp_path: Path, released: FixtureTemplate, run_apply: Apply
) -> None:
    target = make_repo(tmp_path / "repo")
    (target / "AGENTS.md").write_text(
        "# My project\n\nKeep this.\n\n"
        "<!-- bootstrap:begin tdd -->\nstale text\n<!-- bootstrap:end tdd -->\n\n"
        "Keep this too.\n"
    )
    (target / "CLAUDE.md").write_text("# Claude notes\n\nUser rule.\n")

    commit_all(target)

    result = run_apply(target, released.path)

    assert result.returncode == 0, result.stderr
    agents = (target / "AGENTS.md").read_text()
    assert agents.startswith("# My project\n\nKeep this.\n\n")
    assert "Keep this too." in agents
    assert "stale text" not in agents
    assert agents.count("<!-- bootstrap:begin tdd -->") == 1
    assert "Write a failing test first" in agents
    assert "<!-- bootstrap:begin memory -->" in agents
    claude = (target / "CLAUDE.md").read_text()
    assert claude.startswith("# Claude notes\n\nUser rule.\n")
    assert "@AGENTS.md" in claude


def test_unbalanced_marker_fails_and_writes_nothing(
    tmp_path: Path, released: FixtureTemplate, run_apply: Apply
) -> None:
    target = make_repo(tmp_path / "repo")
    agents = "# Mine\n<!-- bootstrap:begin tdd -->\nuser text with no end marker\n"
    (target / "AGENTS.md").write_text(agents)

    commit_all(target)

    result = run_apply(target, released.path)

    assert result.returncode != 0
    assert (target / "AGENTS.md").read_text() == agents
    assert tree(target) == {"AGENTS.md"}


def test_symlinked_agent_file_fails_and_stays_a_symlink(
    tmp_path: Path, released: FixtureTemplate, run_apply: Apply
) -> None:
    target = make_repo(tmp_path / "repo")
    (target / "AGENTS.md").write_text("# Rules\n")
    (target / "CLAUDE.md").symlink_to("AGENTS.md")

    commit_all(target)

    result = run_apply(target, released.path)

    assert result.returncode != 0
    assert (target / "CLAUDE.md").is_symlink()
    assert (target / "AGENTS.md").read_text() == "# Rules\n"
    assert ".copier-answers.yml" not in tree(target)


def test_fenced_markers_and_unknown_sections_stay_unchanged(
    tmp_path: Path, released: FixtureTemplate, run_apply: Apply
) -> None:
    target = make_repo(tmp_path / "repo")
    fenced = (
        "```\n<!-- bootstrap:begin tdd -->\nexample\n<!-- bootstrap:end tdd -->\n```\n"
        "<!-- bootstrap:begin custom -->\nmine\n<!-- bootstrap:end custom -->\n"
    )
    (target / "AGENTS.md").write_text(fenced)

    commit_all(target)

    result = run_apply(target, released.path)

    assert result.returncode == 0, result.stderr
    text = (target / "AGENTS.md").read_text()
    assert text.startswith(fenced)
    assert "<!-- bootstrap:begin custom -->\nmine\n" in text
    assert "<!-- bootstrap:begin tdd -->\nexample\n" in text
    assert "Write a failing test first" in text


def test_crlf_files_keep_their_line_endings(
    tmp_path: Path, released: FixtureTemplate, run_apply: Apply
) -> None:
    target = make_repo(tmp_path / "repo")
    (target / "AGENTS.md").write_bytes(b"# Mine\r\n\r\nUser text.\r\n")

    commit_all(target)

    result = run_apply(target, released.path)

    assert result.returncode == 0, result.stderr
    data = (target / "AGENTS.md").read_bytes()
    assert data.startswith(b"# Mine\r\n\r\nUser text.\r\n")
    assert b"\n" not in data.replace(b"\r\n", b"")
    assert b"<!-- bootstrap:begin tdd -->\r\n" in data


def test_fence_line_with_info_string_does_not_close_a_fence(
    tmp_path: Path, released: FixtureTemplate, run_apply: Apply
) -> None:
    target = make_repo(tmp_path / "repo")
    example = (
        "```\n```python\n<!-- bootstrap:begin tdd -->\nexample\n<!-- bootstrap:end tdd -->\n```\n"
    )
    (target / "AGENTS.md").write_text(example)

    commit_all(target)

    result = run_apply(target, released.path)

    assert result.returncode == 0, result.stderr
    text = (target / "AGENTS.md").read_text()
    assert text.startswith(example)
    assert "<!-- bootstrap:begin tdd -->\nexample\n" in text


def test_foreign_answers_file_fails_and_is_kept(
    tmp_path: Path, released: FixtureTemplate, run_apply: Apply
) -> None:
    target = make_repo(tmp_path / "repo")
    foreign = "_commit: v1\n_src_path: https://example.com/other-template.git\nname: x\n"
    (target / ".copier-answers.yml").write_text(foreign)

    commit_all(target)

    result = run_apply(target, released.path)

    assert result.returncode != 0
    assert (target / ".copier-answers.yml").read_text() == foreign
    assert tree(target) == {".copier-answers.yml"}


def test_claude_md_has_marked_sections_that_state_each_rule(
    tmp_path: Path, released: FixtureTemplate, run_apply: Apply
) -> None:
    target = make_repo(tmp_path / "repo")

    result = run_apply(target, released.path)

    assert result.returncode == 0, result.stderr
    text = (target / "CLAUDE.md").read_text()
    for name, phrases in CLAUDE_SECTIONS.items():
        for phrase in phrases:
            assert phrase in section(text, name), (name, phrase)

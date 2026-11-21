"""Tests run the `bootstrap` command as a subprocess against a temporary repo."""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BOOTSTRAP = Path(sys.executable).parent / "bootstrap"


def run_apply(target: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(BOOTSTRAP), "apply", str(target), "--template", str(REPO_ROOT)],
        capture_output=True,
        text=True,
        check=False,
    )


def make_empty_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


def tree(path: Path) -> set[str]:
    return {
        str(p.relative_to(path))
        for p in path.rglob("*")
        if p.is_file() and ".git" not in p.relative_to(path).parts
    }


def test_apply_to_empty_repo_writes_agent_docs_and_answers_file(tmp_path: Path) -> None:
    target = make_empty_repo(tmp_path / "repo")

    result = run_apply(target)

    assert result.returncode == 0, result.stderr
    assert tree(target) == {"AGENTS.md", "CLAUDE.md", ".copier-answers.yml"}


AGENTS_SECTIONS = {
    "tdd": "Write a failing test first",
    "libraries": "Prefer a maintained third-party library",
    "scripts": "Each repeatable process has a committed script",
    "issues": "search for related issues and pull requests",
    "memory": "Decisions go in `docs/adr/`",
}


def test_agents_md_has_marked_sections_that_state_each_rule(tmp_path: Path) -> None:
    target = make_empty_repo(tmp_path / "repo")

    result = run_apply(target)

    assert result.returncode == 0, result.stderr
    text = (target / "AGENTS.md").read_text()
    for name, phrase in AGENTS_SECTIONS.items():
        begin = text.index(f"<!-- bootstrap:begin {name} -->")
        end = text.index(f"<!-- bootstrap:end {name} -->")
        assert begin < end
        assert phrase in text[begin:end]
    assert "Domain terms go in `GLOSSARY.md`" in text
    assert "Rules for how to work go in `AGENTS.md`" in text


def test_claude_md_imports_agents_md_in_a_marked_section(tmp_path: Path) -> None:
    target = make_empty_repo(tmp_path / "repo")

    result = run_apply(target)

    assert result.returncode == 0, result.stderr
    text = (target / "CLAUDE.md").read_text()
    begin = text.index("<!-- bootstrap:begin agents-import -->")
    end = text.index("<!-- bootstrap:end agents-import -->")
    assert "@AGENTS.md" in text[begin:end].splitlines()


def test_missing_target_fails_and_is_not_created(tmp_path: Path) -> None:
    target = tmp_path / "missing"

    result = run_apply(target)

    assert result.returncode != 0
    assert not target.exists()


def test_existing_agent_files_keep_user_text_and_get_the_sections(tmp_path: Path) -> None:
    target = make_empty_repo(tmp_path / "repo")
    (target / "AGENTS.md").write_text(
        "# My project\n\nKeep this.\n\n"
        "<!-- bootstrap:begin tdd -->\nstale text\n<!-- bootstrap:end tdd -->\n\n"
        "Keep this too.\n"
    )
    (target / "CLAUDE.md").write_text("# Claude notes\n\nUser rule.\n")

    result = run_apply(target)

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


def test_unbalanced_marker_fails_and_writes_nothing(tmp_path: Path) -> None:
    target = make_empty_repo(tmp_path / "repo")
    agents = "# Mine\n<!-- bootstrap:begin tdd -->\nuser text with no end marker\n"
    (target / "AGENTS.md").write_text(agents)

    result = run_apply(target)

    assert result.returncode != 0
    assert (target / "AGENTS.md").read_text() == agents
    assert tree(target) == {"AGENTS.md"}


def test_symlinked_agent_file_fails_and_stays_a_symlink(tmp_path: Path) -> None:
    target = make_empty_repo(tmp_path / "repo")
    (target / "AGENTS.md").write_text("# Rules\n")
    (target / "CLAUDE.md").symlink_to("AGENTS.md")

    result = run_apply(target)

    assert result.returncode != 0
    assert (target / "CLAUDE.md").is_symlink()
    assert (target / "AGENTS.md").read_text() == "# Rules\n"
    assert ".copier-answers.yml" not in tree(target)


def test_fenced_markers_and_unknown_sections_stay_unchanged(tmp_path: Path) -> None:
    target = make_empty_repo(tmp_path / "repo")
    fenced = (
        "```\n<!-- bootstrap:begin tdd -->\nexample\n<!-- bootstrap:end tdd -->\n```\n"
        "<!-- bootstrap:begin custom -->\nmine\n<!-- bootstrap:end custom -->\n"
    )
    (target / "AGENTS.md").write_text(fenced)

    result = run_apply(target)

    assert result.returncode == 0, result.stderr
    text = (target / "AGENTS.md").read_text()
    assert text.startswith(fenced)
    assert "<!-- bootstrap:begin custom -->\nmine\n" in text
    assert "<!-- bootstrap:begin tdd -->\nexample\n" in text
    assert "Write a failing test first" in text


def test_crlf_files_keep_their_line_endings(tmp_path: Path) -> None:
    target = make_empty_repo(tmp_path / "repo")
    (target / "AGENTS.md").write_bytes(b"# Mine\r\n\r\nUser text.\r\n")

    result = run_apply(target)

    assert result.returncode == 0, result.stderr
    data = (target / "AGENTS.md").read_bytes()
    assert data.startswith(b"# Mine\r\n\r\nUser text.\r\n")
    assert b"\n" not in data.replace(b"\r\n", b"")
    assert b"<!-- bootstrap:begin tdd -->\r\n" in data


def test_fence_line_with_info_string_does_not_close_a_fence(tmp_path: Path) -> None:
    target = make_empty_repo(tmp_path / "repo")
    example = (
        "```\n```python\n<!-- bootstrap:begin tdd -->\nexample\n<!-- bootstrap:end tdd -->\n```\n"
    )
    (target / "AGENTS.md").write_text(example)

    result = run_apply(target)

    assert result.returncode == 0, result.stderr
    text = (target / "AGENTS.md").read_text()
    assert text.startswith(example)
    assert "<!-- bootstrap:begin tdd -->\nexample\n" in text


def test_foreign_answers_file_fails_and_is_kept(tmp_path: Path) -> None:
    target = make_empty_repo(tmp_path / "repo")
    foreign = "_commit: v1\n_src_path: https://example.com/other-template.git\nname: x\n"
    (target / ".copier-answers.yml").write_text(foreign)

    result = run_apply(target)

    assert result.returncode != 0
    assert (target / ".copier-answers.yml").read_text() == foreign
    assert tree(target) == {".copier-answers.yml"}


def test_answers_file_from_this_template_is_accepted(tmp_path: Path) -> None:
    target = make_empty_repo(tmp_path / "repo")
    assert run_apply(target).returncode == 0

    result = run_apply(target)

    assert result.returncode == 0, result.stderr

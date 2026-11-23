"""The rendered pre-commit config, the limit text of AGENTS.md, the pins and reapplying."""

import re
from pathlib import Path

import pytest
from conftest import FixtureTemplate, commit_all, git
from pipeline_support import (
    LANGUAGE_SETS,
    MAKEFILE,
    TYPES,
    Apply,
    applied_repo,
    length_hook,
)


@pytest.mark.parametrize("name", LANGUAGE_SETS)
def test_length_hook_covers_the_detected_languages(
    tmp_path: Path, released: FixtureTemplate, run_apply: Apply, name: str
) -> None:
    languages = LANGUAGE_SETS[name]
    target = applied_repo(tmp_path, released, run_apply, languages)

    hook = length_hook(target)

    if not languages:
        # pre-commit installs the environment of each selected hook, even one with no files.
        assert hook["language"] == "unsupported"
        assert hook["files"] == "^$"
        assert "types_or" not in hook
        return
    assert hook["language"] == "golang"
    assert hook["language_version"] == "1.26.4"
    assert hook["additional_dependencies"] == ["github.com/boyter/scc/v4@v4.1.0"]
    assert hook["entry"] == "sh scripts/check-file-length"
    assert hook["args"] == ["--limit", "500"]
    assert hook["types_or"] == [t for language in languages for t in TYPES[language]]


def test_the_limit_in_agents_md_is_the_limit_of_the_hook(
    tmp_path: Path, released: FixtureTemplate, run_apply: Apply
) -> None:
    target = applied_repo(tmp_path, released, run_apply, ["python"])

    stated = re.findall(r"at most (\d+) code lines", (target / "AGENTS.md").read_text())

    assert stated == [length_hook(target)["args"][1]]


def test_the_tools_are_pinned_to_exact_versions() -> None:
    makefile = MAKEFILE.read_text()

    assert re.search(r"^PRE_COMMIT_VERSION := \d+\.\d+\.\d+$", makefile, re.MULTILINE)
    timestamp = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z"
    assert re.search(
        rf"--python 3\.\d+ --exclude-newer {timestamp} pre-commit@\$\(PRE_COMMIT_VERSION\)",
        makefile,
    )


def test_a_second_apply_changes_no_file(
    tmp_path: Path, released: FixtureTemplate, run_apply: Apply
) -> None:
    target = applied_repo(tmp_path, released, run_apply, ["python"])
    commit_all(target)

    again = run_apply(target, released.path)

    assert again.returncode == 0, again.stderr
    assert git(target, "status", "--porcelain") == ""

"""Tests for versioned runs and updates. They run `bootstrap apply` as a subprocess."""

from collections.abc import Callable
from pathlib import Path
from subprocess import CompletedProcess

import pytest
import yaml
from conftest import FixtureTemplate, commit_all, git, make_repo

Apply = Callable[..., CompletedProcess[str]]


def answers(target: Path) -> dict[str, object]:
    return dict(yaml.safe_load((target / ".copier-answers.yml").read_text()))


def test_template_without_a_version_tag_fails_and_writes_nothing(
    tmp_path: Path, template: FixtureTemplate, run_apply: Apply
) -> None:
    template.release("v0.1.0rc1")
    target = make_repo(tmp_path / "repo")

    result = run_apply(target, template.path)

    assert result.returncode != 0
    assert "no release tag" in result.stderr
    assert not (target / ".copier-answers.yml").exists()
    assert not (target / "AGENTS.md").exists()


def test_template_with_no_tag_or_only_a_non_version_tag_fails(
    tmp_path: Path, template: FixtureTemplate, run_apply: Apply
) -> None:
    commit_all(template.path, "untagged")
    target = make_repo(tmp_path / "repo")
    untagged = run_apply(target, template.path)
    git(template.path, "tag", "latest")
    non_version = run_apply(target, template.path)

    for result in (untagged, non_version):
        assert result.returncode != 0
        assert "no release tag" in result.stderr
    assert [p.name for p in target.iterdir()] == [".git"]


def test_default_version_is_the_latest_release_tag(
    tmp_path: Path, template: FixtureTemplate, run_apply: Apply
) -> None:
    template.release("v0.1.0")
    template.release("v0.2.0")
    template.release("v0.3.0rc1")
    target = make_repo(tmp_path / "repo")

    result = run_apply(target, template.path)

    assert result.returncode == 0, result.stderr
    assert answers(target)["_commit"] == "v0.2.0"


def test_explicit_version_is_used_over_the_latest(
    tmp_path: Path, template: FixtureTemplate, run_apply: Apply
) -> None:
    template.release("v0.1.0")
    template.release("v0.2.0")
    target = make_repo(tmp_path / "repo")

    result = run_apply(target, template.path, "--template-version", "v0.1.0")

    assert result.returncode == 0, result.stderr
    assert answers(target)["_commit"] == "v0.1.0"


def test_unknown_version_fails_and_writes_nothing(
    tmp_path: Path, template: FixtureTemplate, run_apply: Apply
) -> None:
    template.release("v0.1.0")
    target = make_repo(tmp_path / "repo")

    result = run_apply(target, template.path, "--template-version", "v9.9.9")

    assert result.returncode != 0
    assert "v9.9.9 is not a version tag" in result.stderr
    assert "Traceback" not in result.stderr
    assert not (target / ".copier-answers.yml").exists()


def test_prerelease_tag_can_be_requested_by_name(
    tmp_path: Path, template: FixtureTemplate, run_apply: Apply
) -> None:
    template.release("v0.1.0")
    template.release("v0.2.0rc1")
    target = make_repo(tmp_path / "repo")

    result = run_apply(target, template.path, "--template-version", "v0.2.0rc1")

    assert result.returncode == 0, result.stderr
    assert answers(target)["_commit"] == "v0.2.0rc1"


def dirty_tracked(target: Path) -> None:
    (target / "notes.txt").write_text("one\n")
    commit_all(target)
    (target / "notes.txt").write_text("two\n")


def dirty_untracked(target: Path) -> None:
    (target / "scratch.txt").write_text("x\n")


def dirty_untracked_hidden_by_config(target: Path) -> None:
    git(target, "config", "status.showUntrackedFiles", "no")
    (target / "scratch.txt").write_text("x\n")


@pytest.mark.parametrize(
    "dirty", [dirty_tracked, dirty_untracked, dirty_untracked_hidden_by_config]
)
def test_uncommitted_changes_fail_and_write_nothing(
    tmp_path: Path, template: FixtureTemplate, run_apply: Apply, dirty: Callable[[Path], None]
) -> None:
    template.release("v0.1.0")
    target = make_repo(tmp_path / "repo")
    dirty(target)
    before = sorted(p.name for p in target.iterdir() if p.name != ".git")

    result = run_apply(target, template.path)

    assert result.returncode != 0
    assert "uncommitted" in result.stderr
    assert sorted(p.name for p in target.iterdir() if p.name != ".git") == before


def test_directory_that_is_not_a_repo_fails(
    tmp_path: Path, template: FixtureTemplate, run_apply: Apply
) -> None:
    template.release("v0.1.0")
    target = tmp_path / "plain"
    target.mkdir()

    result = run_apply(target, template.path)

    assert result.returncode != 0
    assert "git repo" in result.stderr
    assert list(target.iterdir()) == []


def test_subdirectory_of_a_repo_fails(
    tmp_path: Path, template: FixtureTemplate, run_apply: Apply
) -> None:
    template.release("v0.1.0")
    repo = make_repo(tmp_path / "repo")
    target = repo / "sub"
    target.mkdir()

    result = run_apply(target, template.path)

    assert result.returncode != 0
    assert "root" in result.stderr
    assert list(target.iterdir()) == []


@pytest.mark.parametrize("spelling", ["{path}", "{path}/"])
def test_rerun_with_the_same_version_changes_nothing(
    tmp_path: Path, template: FixtureTemplate, run_apply: Apply, spelling: str
) -> None:
    template.release("v0.1.0")
    target = make_repo(tmp_path / "repo")
    assert run_apply(target, template.path).returncode == 0
    commit_all(target)

    result = run_apply(target, spelling.format(path=template.path))

    assert result.returncode == 0, result.stderr
    assert git(target, "status", "--porcelain") == ""


def test_update_merges_a_user_edit_with_the_new_version(
    tmp_path: Path, template: FixtureTemplate, run_apply: Apply
) -> None:
    template.release("v0.1.0", {"template/notes.txt": "one\ntwo\nthree\n"})
    target = make_repo(tmp_path / "repo")
    assert run_apply(target, template.path).returncode == 0
    (target / "notes.txt").write_text("ONE (mine)\ntwo\nthree\n")
    commit_all(target)
    template.release("v0.2.0", {"template/notes.txt": "one\ntwo\nTHREE (new)\n"})

    result = run_apply(target, template.path)

    assert result.returncode == 0, result.stderr
    assert (target / "notes.txt").read_text() == "ONE (mine)\ntwo\nTHREE (new)\n"
    assert answers(target)["_commit"] == "v0.2.0"


def test_update_replaces_only_the_marked_sections(
    tmp_path: Path, template: FixtureTemplate, run_apply: Apply
) -> None:
    template.release("v0.1.0")
    target = make_repo(tmp_path / "repo")
    assert run_apply(target, template.path).returncode == 0
    agents = (target / "AGENTS.md").read_text()
    (target / "AGENTS.md").write_text("# Mine\n\nBefore.\n\n" + agents + "\nAfter.\n")
    (target / "CLAUDE.md").write_text("# Claude mine\n\n" + (target / "CLAUDE.md").read_text())
    commit_all(target)
    tdd = (template.path / "sections/agents/tdd.md").read_text()
    template.release("v0.2.0", {"sections/agents/tdd.md": tdd + "\nNew rule from v0.2.0.\n"})

    result = run_apply(target, template.path)

    assert result.returncode == 0, result.stderr
    text = (target / "AGENTS.md").read_text()
    assert text.startswith("# Mine\n\nBefore.\n\n")
    assert text.endswith("\nAfter.\n")
    assert text.count("<!-- bootstrap:begin tdd -->") == 1
    begin = text.index("<!-- bootstrap:begin tdd -->\n") + len("<!-- bootstrap:begin tdd -->\n")
    end = text.index("\n<!-- bootstrap:end tdd -->")
    assert text[begin:end] == (template.path / "sections/agents/tdd.md").read_text().strip()
    claude = (target / "CLAUDE.md").read_text()
    assert claude.startswith("# Claude mine\n\n")
    assert claude.count("<!-- bootstrap:begin agents-import -->") == 1
    assert "@AGENTS.md" in claude


def test_update_keeps_a_user_edit_to_a_skip_if_exists_file(
    tmp_path: Path, template: FixtureTemplate, run_apply: Apply
) -> None:
    copier_yml = (template.path / "copier.yml").read_text()
    template.release(
        "v0.1.0",
        {
            "copier.yml": copier_yml + "_skip_if_exists:\n  - policy.md\n",
            "template/policy.md": "default\n",
        },
    )
    target = make_repo(tmp_path / "repo")
    assert run_apply(target, template.path).returncode == 0
    (target / "policy.md").write_text("my policy\n")
    commit_all(target)
    template.release("v0.2.0", {"template/policy.md": "default, changed\n"})

    result = run_apply(target, template.path)

    assert result.returncode == 0, result.stderr
    assert (target / "policy.md").read_text() == "my policy\n"
    assert answers(target)["_commit"] == "v0.2.0"


def test_downgrade_fails_and_changes_nothing(
    tmp_path: Path, template: FixtureTemplate, run_apply: Apply
) -> None:
    template.release("v0.1.0", {"template/notes.txt": "one\n"})
    template.release("v0.2.0", {"template/notes.txt": "two\n"})
    target = make_repo(tmp_path / "repo")
    assert run_apply(target, template.path).returncode == 0
    commit_all(target)

    result = run_apply(target, template.path, "--template-version", "v0.1.0")

    assert result.returncode != 0
    assert "downgrading" in result.stderr
    assert git(target, "status", "--porcelain") == ""
    assert answers(target)["_commit"] == "v0.2.0"


def test_update_conflict_names_the_files_and_exits_nonzero(
    tmp_path: Path, template: FixtureTemplate, run_apply: Apply
) -> None:
    template.release("v0.1.0", {"template/notes.txt": "one\ntwo\nthree\n"})
    target = make_repo(tmp_path / "repo")
    assert run_apply(target, template.path).returncode == 0
    (target / "notes.txt").write_text("one\nTWO (mine)\nthree\n")
    commit_all(target)
    template.release("v0.2.0", {"template/notes.txt": "one\nTWO (new)\nthree\n"})

    result = run_apply(target, template.path)

    assert result.returncode == 1
    assert "notes.txt" in result.stderr
    assert "<<<<<<<" in (target / "notes.txt").read_text()


PYTHON_FILE = "template/{% if 'python' in languages %}python.txt{% endif %}.jinja"


def saved_languages(target: Path) -> set[str]:
    value = answers(target)["languages"]
    assert isinstance(value, list)
    return set(value)


def test_newly_detected_language_adds_its_files_on_update(
    tmp_path: Path, template: FixtureTemplate, run_apply: Apply
) -> None:
    template.release("v0.1.0", {PYTHON_FILE: "one\ntwo\nthree\n"})
    target = make_repo(tmp_path / "repo")
    assert run_apply(target, template.path).returncode == 0
    assert not (target / "python.txt").exists()
    (target / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    commit_all(target)
    template.release("v0.2.0")

    result = run_apply(target, template.path)

    assert result.returncode == 0, result.stderr
    assert (target / "python.txt").read_text() == "one\ntwo\nthree\n"
    assert saved_languages(target) == {"python"}


def test_user_edit_to_a_language_file_survives_an_update(
    tmp_path: Path, template: FixtureTemplate, run_apply: Apply
) -> None:
    template.release("v0.1.0", {PYTHON_FILE: "one\ntwo\nthree\n"})
    target = make_repo(tmp_path / "repo")
    (target / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    commit_all(target)
    assert run_apply(target, template.path).returncode == 0
    (target / "python.txt").write_text("ONE (mine)\ntwo\nthree\n")
    commit_all(target)
    template.release("v0.2.0", {PYTHON_FILE: "one\ntwo\nTHREE (new)\n"})

    result = run_apply(target, template.path)

    assert result.returncode == 0, result.stderr
    assert (target / "python.txt").read_text() == "ONE (mine)\ntwo\nTHREE (new)\n"


def test_a_language_stays_when_its_marker_file_is_removed(
    tmp_path: Path, template: FixtureTemplate, run_apply: Apply
) -> None:
    template.release("v0.1.0", {PYTHON_FILE: "one\n"})
    target = make_repo(tmp_path / "repo")
    (target / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    commit_all(target)
    assert run_apply(target, template.path).returncode == 0
    (target / "python.txt").write_text("one\nmy edit\n")
    (target / "pyproject.toml").unlink()
    commit_all(target)
    template.release("v0.2.0")

    result = run_apply(target, template.path)

    assert result.returncode == 0, result.stderr
    assert (target / "python.txt").read_text() == "one\nmy edit\n"
    assert saved_languages(target) == {"python"}


def test_each_language_marker_file_is_detected(
    tmp_path: Path, template: FixtureTemplate, run_apply: Apply
) -> None:
    template.release("v0.1.0")
    target = make_repo(tmp_path / "repo")
    for name in ("pyproject.toml", "go.mod", "Cargo.toml"):
        (target / name).write_text("")
    commit_all(target)

    result = run_apply(target, template.path)

    assert result.returncode == 0, result.stderr
    assert saved_languages(target) == {"python", "go", "rust"}


def applied_and_committed(tmp_path: Path, template: FixtureTemplate, run_apply: Apply) -> Path:
    template.release("v0.1.0")
    target = make_repo(tmp_path / "repo")
    assert run_apply(target, template.path).returncode == 0
    commit_all(target)
    return target


def set_answer(target: Path, key: str, value: object | None) -> None:
    data = answers(target)
    if value is None:
        del data[key]
    else:
        data[key] = value
    (target / ".copier-answers.yml").write_text(yaml.safe_dump(data))
    commit_all(target)


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("languages", ["cobol"], "cobol"),
        ("languages", "python", "must be a list"),
        ("_commit", None, "no _commit"),
        ("_commit", "v7.7.7", "v7.7.7"),
    ],
)
def test_bad_saved_answers_fail_without_a_traceback_and_change_nothing(
    tmp_path: Path,
    template: FixtureTemplate,
    run_apply: Apply,
    key: str,
    value: object | None,
    expected: str,
) -> None:
    target = applied_and_committed(tmp_path, template, run_apply)
    set_answer(target, key, value)
    template.release("v0.2.0")

    result = run_apply(target, template.path)

    assert result.returncode != 0
    assert expected in result.stderr
    assert "Traceback" not in result.stderr
    assert git(target, "status", "--porcelain") == ""


def test_removed_section_file_fails_before_anything_is_written(
    tmp_path: Path, template: FixtureTemplate, run_apply: Apply
) -> None:
    target = applied_and_committed(tmp_path, template, run_apply)
    template.release("v0.2.0", {"sections/agents/tdd.md": None, "template/notes.txt": "new\n"})

    result = run_apply(target, template.path)

    assert result.returncode != 0
    assert "tdd.md" in result.stderr
    assert "Traceback" not in result.stderr
    assert git(target, "status", "--porcelain") == ""


def test_template_directory_that_is_not_a_git_repo_fails(tmp_path: Path, run_apply: Apply) -> None:
    source = tmp_path / "plain-template"
    source.mkdir()
    target = make_repo(tmp_path / "repo")

    result = run_apply(target, source)

    assert result.returncode != 0
    assert "cannot list the tags" in result.stderr
    assert "Traceback" not in result.stderr
    assert list(p.name for p in target.iterdir()) == [".git"]


def test_shortcut_source_fails_with_a_one_line_error(tmp_path: Path, run_apply: Apply) -> None:
    target = make_repo(tmp_path / "repo")

    result = run_apply(target, "gh:owner/repo")

    assert result.returncode != 0
    assert "shortcut" in result.stderr
    assert "Traceback" not in result.stderr
    assert list(p.name for p in target.iterdir()) == [".git"]


def test_update_does_not_overwrite_an_ignored_file(
    tmp_path: Path, template: FixtureTemplate, run_apply: Apply
) -> None:
    target = applied_and_committed(tmp_path, template, run_apply)
    (target / ".gitignore").write_text("secret.txt\n")
    (target / "secret.txt").write_text("my secret\n")
    commit_all(target)
    assert git(target, "status", "--porcelain") == ""
    template.release("v0.2.0", {"template/secret.txt": "from the template\n"})

    result = run_apply(target, template.path)

    assert result.returncode != 0
    assert "secret.txt" in result.stderr
    assert (target / "secret.txt").read_text() == "my secret\n"
    assert answers(target)["_commit"] == "v0.1.0"


def test_git_plus_prefixed_source_applies_and_reruns_cleanly(
    tmp_path: Path, template: FixtureTemplate, run_apply: Apply
) -> None:
    template.release("v0.1.0")
    target = make_repo(tmp_path / "repo")
    source = f"git+file://{template.path}"

    first = run_apply(target, source)
    commit_all(target)
    second = run_apply(target, source)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert git(target, "status", "--porcelain") == ""


def test_home_relative_source_applies_and_reruns_cleanly(tmp_path: Path, run_apply: Apply) -> None:
    home = tmp_path / "home"
    home.mkdir()
    template = FixtureTemplate(home / "template-src")
    template.release("v0.1.0")
    target = make_repo(tmp_path / "repo")
    env = {"HOME": str(home)}

    first = run_apply(target, "~/template-src", env=env)
    commit_all(target)
    second = run_apply(target, "~/template-src", env=env)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert git(target, "status", "--porcelain") == ""
    assert str(home / "template-src") in (target / ".copier-answers.yml").read_text()


def test_update_from_a_relative_source_stored_by_an_earlier_run(
    tmp_path: Path, template: FixtureTemplate, run_apply: Apply
) -> None:
    template.release("v0.1.0")
    target = make_repo(tmp_path / "repo")
    assert run_apply(target, template.path).returncode == 0
    set_answer(target, "_src_path", "../template-src")
    template.release("v0.2.0")

    result = run_apply(target, "../template-src", cwd=target)

    assert result.returncode == 0, result.stderr
    assert answers(target)["_commit"] == "v0.2.0"


def ignore_file(target: Path, *names: str) -> None:
    (target / ".gitignore").write_text("".join(f"{name}\n" for name in names))
    commit_all(target)
    assert git(target, "status", "--porcelain") == ""


def test_update_does_not_delete_an_ignored_file_that_the_new_version_drops(
    tmp_path: Path, template: FixtureTemplate, run_apply: Apply
) -> None:
    template.release("v0.1.0", {"template/local.cfg": "default\n"})
    target = make_repo(tmp_path / "repo")
    assert run_apply(target, template.path).returncode == 0
    commit_all(target)
    git(target, "rm", "-q", "--cached", "local.cfg")
    (target / "local.cfg").write_text("my edit\n")
    ignore_file(target, "local.cfg")
    template.release("v0.2.0", {"template/local.cfg": None})

    result = run_apply(target, template.path)

    assert result.returncode != 0
    assert "local.cfg" in result.stderr
    assert (target / "local.cfg").read_text() == "my edit\n"


def test_rerun_works_when_the_template_ignores_a_file_that_it_renders(
    tmp_path: Path, template: FixtureTemplate, run_apply: Apply
) -> None:
    template.release(
        "v0.1.0", {"template/local.cfg": "default\n", "template/.gitignore.jinja": "local.cfg\n"}
    )
    target = make_repo(tmp_path / "repo")
    assert run_apply(target, template.path).returncode == 0
    commit_all(target)
    template.release("v0.2.0", {"template/local.cfg": "changed\n"})

    result = run_apply(target, template.path)

    assert result.returncode == 0, result.stderr
    assert (target / "local.cfg").read_text() == "changed\n"


def test_an_ignored_skip_if_exists_file_does_not_block_updates(
    tmp_path: Path, template: FixtureTemplate, run_apply: Apply
) -> None:
    copier_yml = (template.path / "copier.yml").read_text()
    template.release(
        "v0.1.0",
        {"copier.yml": copier_yml + "_skip_if_exists:\n  - .env\n", "template/.env": "x\n"},
    )
    target = make_repo(tmp_path / "repo")
    assert run_apply(target, template.path).returncode == 0
    (target / ".env").write_text("SECRET=1\n")
    ignore_file(target, ".env")
    template.release("v0.2.0", {"template/.env": "y\n", "template/notes.txt": "new\n"})

    result = run_apply(target, template.path)

    assert result.returncode == 0, result.stderr
    assert (target / ".env").read_text() == "SECRET=1\n"
    assert (target / "notes.txt").read_text() == "new\n"


def test_symlinked_target_path_updates_cleanly(
    tmp_path: Path, template: FixtureTemplate, run_apply: Apply
) -> None:
    template.release("v0.1.0", {"template/notes.txt": "one\n"})
    repo = make_repo(tmp_path / "repo")
    link = tmp_path / "link"
    link.symlink_to(repo)
    assert run_apply(link, template.path).returncode == 0
    commit_all(repo)
    template.release("v0.2.0", {"template/notes.txt": "two\n"})

    result = run_apply(link, template.path)

    assert result.returncode == 0, result.stderr
    assert (repo / "notes.txt").read_text() == "two\n"


def test_first_run_stops_when_the_template_would_replace_a_tracked_file(
    tmp_path: Path, template: FixtureTemplate, run_apply: Apply
) -> None:
    template.release("v0.1.0", {"template/notes.txt": "from the template\n"})
    target = make_repo(tmp_path / "repo")
    (target / "notes.txt").write_text("mine\n")
    commit_all(target)

    result = run_apply(target, template.path)

    assert result.returncode != 0
    assert "notes.txt" in result.stderr
    assert "Traceback" not in result.stderr
    assert (target / "notes.txt").read_text() == "mine\n"
    assert git(target, "status", "--porcelain") == ""


def set_up_ignored_template_file(
    tmp_path: Path, template: FixtureTemplate, run_apply: Apply, *, skip: bool = False
) -> Path:
    """Apply v0.1.0 with a generated file, then ignore it and put the user's text in it."""
    name = ".env" if skip else "local.cfg"
    files = {f"template/{name}": "default\n"}
    if skip:
        files["copier.yml"] = (
            template.path / "copier.yml"
        ).read_text() + f"_skip_if_exists:\n  - {name}\n"
    else:
        files["template/.gitignore.jinja"] = f"{name}\n"
    template.release("v0.1.0", files)
    target = make_repo(tmp_path / "repo")
    assert run_apply(target, template.path).returncode == 0
    commit_all(target)
    (target / name).write_text("SECRET=1\n")
    if skip:
        git(target, "rm", "-q", "--cached", name)
        ignore_file(target, name)
    assert git(target, "status", "--porcelain") == ""
    return target


def test_edited_ignored_file_that_both_versions_render_is_kept_on_a_rerun(
    tmp_path: Path, template: FixtureTemplate, run_apply: Apply
) -> None:
    target = set_up_ignored_template_file(tmp_path, template, run_apply)

    result = run_apply(target, template.path)

    assert result.returncode != 0
    assert "local.cfg" in result.stderr
    assert (target / "local.cfg").read_text() == "SECRET=1\n"


def test_edited_ignored_skip_if_exists_file_survives_when_the_new_version_drops_it(
    tmp_path: Path, template: FixtureTemplate, run_apply: Apply
) -> None:
    target = set_up_ignored_template_file(tmp_path, template, run_apply, skip=True)
    template.release("v0.2.0", {"template/.env": None})

    result = run_apply(target, template.path)

    assert result.returncode != 0
    assert ".env" in result.stderr
    assert (target / ".env").read_text() == "SECRET=1\n"


def test_unedited_ignored_generated_file_is_removed_when_the_new_version_drops_it(
    tmp_path: Path, template: FixtureTemplate, run_apply: Apply
) -> None:
    target = set_up_ignored_template_file(tmp_path, template, run_apply)
    (target / "local.cfg").write_text("default\n")
    template.release("v0.2.0", {"template/local.cfg": None})

    result = run_apply(target, template.path)

    assert result.returncode == 0, result.stderr
    assert not (target / "local.cfg").exists()


def test_scratch_render_uses_the_stored_answers(
    tmp_path: Path, template: FixtureTemplate, run_apply: Apply
) -> None:
    copier_yml = (template.path / "copier.yml").read_text()
    template.release(
        "v0.1.0",
        {
            "copier.yml": copier_yml + "ci:\n  type: bool\n  default: false\n",
            "template/{% if ci %}ci.txt{% endif %}.jinja": "default\n",
        },
    )
    target = make_repo(tmp_path / "repo")
    assert run_apply(target, template.path).returncode == 0
    commit_all(target)
    set_answer(target, "ci", True)
    (target / "ci.txt").write_text("my ci\n")
    ignore_file(target, "ci.txt")
    template.release("v0.2.0")

    result = run_apply(target, template.path)

    assert result.returncode != 0
    assert "ci.txt" in result.stderr
    assert (target / "ci.txt").read_text() == "my ci\n"


@pytest.mark.parametrize("flag", ["--skip-worktree", "--assume-unchanged"])
def test_local_edit_hidden_by_an_index_flag_stops_the_run(
    tmp_path: Path, template: FixtureTemplate, run_apply: Apply, flag: str
) -> None:
    template.release("v0.1.0", {"template/notes.txt": "one\ntwo\nthree\n"})
    target = make_repo(tmp_path / "repo")
    assert run_apply(target, template.path).returncode == 0
    commit_all(target)
    git(target, "update-index", flag, "notes.txt")
    (target / "notes.txt").write_text("ONE local only\ntwo\nthree\n")
    assert git(target, "status", "--porcelain") == ""
    template.release("v0.2.0", {"template/notes.txt": "one\ntwo\nTHREE\n"})

    result = run_apply(target, template.path)

    assert result.returncode != 0
    assert "notes.txt" in result.stderr
    assert (target / "notes.txt").read_text() == "ONE local only\ntwo\nthree\n"


def test_scratch_render_uses_the_target_directory_name(
    tmp_path: Path, template: FixtureTemplate, run_apply: Apply
) -> None:
    template.release("v0.1.0")
    target = make_repo(tmp_path / "repo")
    assert run_apply(target, template.path).returncode == 0
    commit_all(target)
    (target / "repo.local").write_text("my secret\n")
    ignore_file(target, "repo.local")
    template.release(
        "v0.2.0", {"template/{{ _copier_conf.dst_path.name }}.local.jinja": "from template\n"}
    )

    result = run_apply(target, template.path)

    assert result.returncode != 0
    assert "repo.local" in result.stderr
    assert (target / "repo.local").read_text() == "my secret\n"


@pytest.mark.parametrize("tracked", [True, False])
def test_update_stops_when_a_rej_file_could_be_removed_by_copier(
    tmp_path: Path, template: FixtureTemplate, run_apply: Apply, tracked: bool
) -> None:
    template.release("v0.1.0", {"template/notes.txt": "one\ntwo\nthree\n"})
    target = make_repo(tmp_path / "repo")
    assert run_apply(target, template.path).returncode == 0
    commit_all(target)
    (target / "notes.txt.rej").write_text("my saved hunks\n")
    if tracked:
        git(target, "add", "-f", "notes.txt.rej")
    ignore_file(target, "*.rej")
    template.release("v0.2.0", {"template/notes.txt": "one\ntwo\nTHREE\n"})

    result = run_apply(target, template.path)

    assert result.returncode != 0 or tracked
    if not tracked:
        assert ".rej" in result.stderr
    assert (target / "notes.txt.rej").read_text() == "my saved hunks\n"


def test_first_run_keeps_an_existing_skip_if_exists_file(
    tmp_path: Path, template: FixtureTemplate, run_apply: Apply
) -> None:
    copier_yml = (template.path / "copier.yml").read_text()
    template.release(
        "v0.1.0",
        {"copier.yml": copier_yml + "_skip_if_exists:\n  - .env\n", "template/.env": "default\n"},
    )
    target = make_repo(tmp_path / "repo")
    (target / ".env").write_text("mine\n")
    commit_all(target)

    result = run_apply(target, template.path)

    assert result.returncode == 0, result.stderr
    assert (target / ".env").read_text() == "mine\n"


def test_ignored_file_that_already_equals_the_new_render_is_not_a_clash(
    tmp_path: Path, template: FixtureTemplate, run_apply: Apply
) -> None:
    template.release(
        "v0.1.0", {"template/local.cfg": "default\n", "template/.gitignore.jinja": "local.cfg\n"}
    )
    target = make_repo(tmp_path / "repo")
    assert run_apply(target, template.path).returncode == 0
    commit_all(target)
    (target / "local.cfg").write_text("changed\n")
    template.release("v0.2.0", {"template/local.cfg": "changed\n"})

    result = run_apply(target, template.path)

    assert result.returncode == 0, result.stderr
    assert (target / "local.cfg").read_text() == "changed\n"


def snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and ".git" not in p.relative_to(root).parts
    }


def test_same_version_rerun_changes_no_file_bytes(
    tmp_path: Path, template: FixtureTemplate, run_apply: Apply
) -> None:
    template.release("v0.1.0", {PYTHON_FILE: "one\ntwo\n", "template/notes.txt": "a\nb\nc\n"})
    target = make_repo(tmp_path / "repo")
    (target / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    commit_all(target)
    assert run_apply(target, template.path).returncode == 0
    (target / "notes.txt").write_text("A (mine)\nb\nc\n")
    commit_all(target)
    before = snapshot(target)

    result = run_apply(target, template.path)

    assert result.returncode == 0, result.stderr
    assert snapshot(target) == before
    assert git(target, "status", "--porcelain", "--ignored") == ""

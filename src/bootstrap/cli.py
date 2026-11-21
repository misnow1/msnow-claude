"""The `bootstrap` command."""

import argparse
import re
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import unquote, urlparse

import copier
import yaml
from copier.errors import CopierError

from bootstrap import languages, markers
from bootstrap.errors import BootstrapError
from bootstrap.github.planner import plan
from bootstrap.github.settings_file import load_desired, load_state
from bootstrap.gitutil import (
    check_clean_repo_root,
    ignored_rej_files,
    tracked_paths,
    unmerged_paths,
)
from bootstrap.versions import resolve_version

DEFAULT_TEMPLATE = "https://github.com/misnow1/msnow-claude.git"
AGENT_FILES = {
    "AGENTS.md": ("agents", ["tdd", "libraries", "scripts", "issues", "memory"]),
    "CLAUDE.md": ("claude", ["agents-import"]),
}


def read_existing(path: Path) -> str | None:
    """Read an agent file, or return None when it does not exist.

    Raise BootstrapError for a symlink, a non-file or invalid markers, before any write.
    """
    if path.is_symlink():
        raise BootstrapError(f"{path.name} is a symlink. Replace it with a regular file first.")
    if not path.exists():
        return None
    if not path.is_file():
        raise BootstrapError(f"{path.name} is not a regular file.")
    with path.open(encoding="utf-8", newline="") as stream:
        text = stream.read()
    try:
        markers.find_sections(text.splitlines(keepends=True))
    except markers.MarkerError as error:
        raise BootstrapError(f"{path.name}: {error}") from error
    return text


def normalize_source(source: str) -> str:
    """Return a comparable form of a template source.

    An existing local path is resolved. A relative path resolves against the current directory,
    because Copier records the source as typed. A URL loses a trailing slash and `.git`.
    """
    source = strip_git_prefix(source)
    path = Path(source).expanduser()
    if path.exists():
        return str(path.resolve())
    if "://" in source or source.startswith("git@"):
        return source.rstrip("/").removesuffix(".git")
    return source


def strip_git_prefix(source: str) -> str:
    """Remove the `git+` prefix, which Copier accepts and `git ls-remote` does not.

    A `file://` URL becomes a local path, which is how Copier reads a local template.
    """
    source = source.removeprefix("git+")
    if source.startswith("file://"):
        return unquote(urlparse(source).path)
    return source


def resolve_local_path(source: str) -> str:
    """Return an existing local path as an absolute path. Return any other source unchanged.

    The `git+` prefix is removed and `~` is expanded, as Copier does.
    """
    source = strip_git_prefix(source)
    path = Path(source).expanduser()
    return str(path.resolve()) if path.exists() else source


def check_answers_file(path: Path, template: str) -> dict[str, object] | None:
    """Return the stored answers, or None without an answers file.

    Raise BootstrapError when the answers file belongs to another template.

    Copier overwrites the answers file, which would break `copier update` for that template.
    """
    if not path.is_file():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise BootstrapError(f"{path.name} is not valid YAML: {error}") from error
    source = data.get("_src_path") if isinstance(data, dict) else None
    if not isinstance(source, str) or normalize_source(source) != normalize_source(template):
        raise BootstrapError(
            f"{path.name} records the template {source!r}, not {template!r}. "
            "Bootstrap would overwrite it and break updates from the other template. "
            "Pass the same source string if both name one template."
        )
    return dict(data)


def read_sections(sections_dir: Path, folder: str, names: list[str]) -> dict[str, str]:
    """Read the section bodies of one agent file from the template checkout."""
    bodies = {}
    for name in names:
        path = sections_dir / folder / f"{name}.md"
        if not path.is_file():
            raise BootstrapError(
                f"the template has no section file {path.relative_to(sections_dir.parent)}"
            )
        bodies[name] = path.read_text(encoding="utf-8")
    return bodies


ANSWERS_FILE = Path(".copier-answers.yml")


@contextmanager
def copier_errors() -> Iterator[None]:
    """Turn the ValueError and TypeError that Copier raises for bad input into BootstrapError."""
    try:
        yield
    except (ValueError, TypeError) as error:
        raise BootstrapError(f"Copier failed: {error}") from error


class Render:
    """What the template renders at one version: each path with its content."""

    def __init__(self, files: dict[Path, bytes], skipped: set[Path]) -> None:
        self.files = files
        # Paths that match `_skip_if_exists`. Copier does not write one when it exists.
        self.skipped = skipped


def render_files(source: str, version: str, data: dict[str, object], dst_name: str) -> Render:
    """Render the template at a version into a scratch directory and read the result.

    The scratch directory has the name of the target, because a template can build file names
    from its destination directory. Template tasks do not run.
    """
    with tempfile.TemporaryDirectory() as scratch:
        dst = Path(scratch) / dst_name
        dst.mkdir()
        return _render(source, version, data, dst)


def _render(source: str, version: str, data: dict[str, object], dst: Path) -> Render:
    with (
        copier_errors(),
        copier.Worker(
            src_path=source,
            dst_path=dst,
            vcs_ref=version,
            data=data,
            defaults=True,
            quiet=True,
            skip_tasks=True,
        ) as worker,
    ):
        worker.run_copy()
        files = {
            path.relative_to(dst): path.read_bytes() for path in dst.rglob("*") if path.is_file()
        }
        files.pop(ANSWERS_FILE, None)
        return Render(files, {path for path in files if worker.match_skip(path)})


def answers_data(stored: dict[str, object] | None, found: list[str]) -> dict[str, object]:
    """Return the answers that Copier uses for a render: the stored answers and the languages."""
    kept = {k: v for k, v in (stored or {}).items() if not k.startswith("_")}
    return {**kept, "languages": found}


def check_template_clashes(
    target: Path,
    source: str,
    version: str,
    found: list[str],
    stored: dict[str, object] | None,
) -> None:
    """Raise BootstrapError when a run would lose a file that git cannot restore.

    The worktree is clean, so a file that exists in target and is not tracked is ignored, and
    git has no copy of it. An update writes every file of the new version over the target. It
    also deletes each file that only the old version renders, including a `_skip_if_exists`
    file. So an untracked file is at risk at each path that either version renders, and the
    run stops when its content is not what the template wrote. A file that equals the old
    render, or the new render, loses nothing.

    A first run stops on any existing file at a rendered path. Copier would ask before it
    replaces the file, and #16 adds the merge for these conflicts.
    """
    new = render_files(source, version, answers_data(stored, found), target.name)
    if stored is None:
        clash = sorted(
            path for path in new.files if path not in new.skipped and (target / path).exists()
        )
        problem = (
            "the template would replace existing files. Merge them into the template version first"
        )
    else:
        old_data = answers_data(stored, languages.saved(stored))
        old = render_files(source, str(stored["_commit"]), old_data, target.name)
        rej = ignored_rej_files(target)
        if rej:
            raise BootstrapError(
                "Copier removes an untracked .rej file during an update. Move these first:\n  "
                + "\n  ".join(rej)
            )
        tracked = tracked_paths(target)
        clash = []
        for path in sorted(set(new.files) | set(old.files)):
            file = target / path
            if str(path) in tracked or not file.is_file():
                continue
            # Copier writes a new-version file unless it is skipped, and removes a dropped file.
            writes = path in new.files and path not in new.skipped
            deletes = path in old.files and path not in new.files
            if not (writes or deletes):
                continue
            known = {new.files.get(path), old.files.get(path)} - {None}
            if file.read_bytes() not in known:
                clash.append(path)
        problem = (
            "the update would overwrite or delete files that git does not track, likely ignored "
            "files, and their content differs from the template. Move them, or un-ignore and "
            "commit them, first"
        )
    if clash:
        raise BootstrapError(f"{problem}:\n  " + "\n  ".join(str(path) for path in clash))


def apply(target: Path, template: str, template_version: str | None = None) -> None:
    target = target.resolve()
    if not target.is_dir():
        raise BootstrapError(f"target is not an existing directory: {target}")
    check_clean_repo_root(target)
    existing = {name: read_existing(target / name) for name in AGENT_FILES}
    stored = check_answers_file(target / ".copier-answers.yml", template)
    if stored is not None and not isinstance(stored.get("_commit"), str):
        raise BootstrapError(
            ".copier-answers.yml has no _commit, so Copier cannot update from it. "
            "Restore the file from git history. To start over, remove the files that the "
            "template wrote, and the answers file, and run again."
        )
    # A language that was saved once stays, because an update deletes the files of a dropped one.
    found = sorted(set(languages.detect(target)) | set(languages.saved(stored)))
    # A rerun reuses the stored string, so another spelling does not rewrite the answers file.
    source = str(stored["_src_path"]) if stored else resolve_local_path(template)
    if re.match(r"^(gh|gl):", source):
        raise BootstrapError(f"{source} is a Copier shortcut. Pass a full git URL or a local path.")
    version = resolve_version(source, template_version)
    check_template_clashes(target, source, version, found, stored)
    with copier_errors():
        worker = copier.Worker(
            src_path=source,
            dst_path=target,
            vcs_ref=version,
            data={"languages": found},
            defaults=True,
            quiet=True,
            overwrite=stored is not None,
        )
    with worker:
        with copier_errors():
            sections_dir = worker.template.local_abspath / "sections"
        # Read every section before Copier writes, so a missing file leaves no half update.
        texts = {
            filename: markers.render(existing[filename], read_sections(sections_dir, folder, names))
            for filename, (folder, names) in AGENT_FILES.items()
        }
        with copier_errors():
            if stored is None:
                worker.run_copy()
            else:
                worker.run_update()
        for filename, text in texts.items():
            markers.write_atomic(target / filename, text)
    conflicts = unmerged_paths(target)
    if conflicts:
        raise BootstrapError(
            "the update left merge conflicts. Resolve them, then commit:\n  "
            + "\n  ".join(conflicts)
        )


def github(target: Path, state: Path) -> None:
    """Print the plan of GitHub operations. Nothing changes on GitHub."""
    target = target.resolve()
    if not target.is_dir():
        raise BootstrapError(f"target is not an existing directory: {target}")
    print(
        plan(load_desired(target), load_state(state)).model_dump_json(indent=2, exclude_none=True)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bootstrap")
    commands = parser.add_subparsers(dest="command", required=True)
    apply_parser = commands.add_parser("apply", help="apply the standards to a repo")
    apply_parser.add_argument("target", type=Path)
    apply_parser.add_argument("--template", default=DEFAULT_TEMPLATE)
    apply_parser.add_argument(
        "--template-version",
        help="template version tag; the default is the latest release tag",
    )
    github_parser = commands.add_parser("github", help="plan the GitHub settings of a repo")
    github_parser.add_argument("target", type=Path)
    github_parser.add_argument(
        "--dry-run",
        action="store_true",
        required=True,
        help="print the plan as JSON and change nothing (the live adapter comes later)",
    )
    github_parser.add_argument("--state", type=Path, required=True, help="GitHub state file")
    args = parser.parse_args(argv)
    try:
        if args.command == "github":
            github(args.target, args.state)
        else:
            apply(args.target, args.template, args.template_version)
    except (BootstrapError, CopierError, OSError, UnicodeDecodeError) as error:
        print(f"bootstrap: error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

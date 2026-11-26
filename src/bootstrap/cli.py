"""The `bootstrap` command."""

import argparse
import json
import re
import signal
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import copier
import yaml
from copier.errors import CopierError

from bootstrap import languages, markers
from bootstrap.errors import BootstrapError
from bootstrap.github.executor import execute
from bootstrap.github.models import CurrentState, DesiredState, Plan, Push, Report
from bootstrap.github.planner import plan
from bootstrap.github.push import push_branch
from bootstrap.github.reader import read_state
from bootstrap.github.repo import Repo, resolve_repo
from bootstrap.github.settings_file import (
    SETTINGS_PATH,
    load_desired,
    load_state,
    parse_desired,
)
from bootstrap.gitutil import (
    COMMIT_MESSAGE,
    check_clean_repo_root,
    check_repo_root,
    commit_written,
    has_branches,
    has_commit,
    has_origin,
    head_has_commit,
    ignored_paths,
    ignored_rej_files,
    push_timeout,
    tracked_paths,
    unmerged_paths,
)
from bootstrap.versions import resolve_version

DEFAULT_TEMPLATE = "https://github.com/misnow1/msnow-claude.git"
AGENT_FILES = {
    "AGENTS.md": (
        "agents",
        ["tdd", "libraries", "scripts", "issues", "memory", "checks", "challenge"],
    ),
    "CLAUDE.md": ("claude", ["agents-import", "workflow", "subagent-model"]),
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
) -> tuple[Render, list[Path]]:
    """Raise BootstrapError when a run would lose a file that git cannot restore.

    Return the render of the new version and the paths that an update deletes.

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
    deleted: list[Path] = []
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
        deleted = sorted(p for p in set(old.files) - set(new.files) if (target / p).exists())
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
    return new, deleted


@dataclass(frozen=True)
class Preflight:
    """What a run needs before it plans: the target, the repo (None for a state file) and state."""

    target: Path
    repo: Repo | None
    current: CurrentState
    branch: str | None  # the local branch to push, for an empty repo


def resolve_target(target: Path) -> Path:
    target = target.resolve()
    if not target.is_dir():
        raise BootstrapError(f"target is not an existing directory: {target}")
    return target


def preflight(target: Path, state: Path | None) -> Preflight:
    """Read the state and check the branch. It needs no settings, so `apply` can run it first."""
    target = resolve_target(target)
    repo = None
    if state is None:
        # In a subdirectory, git would give the remote of the outer repo.
        check_repo_root(target)
        repo = resolve_repo(target)
        print(f"bootstrap: GitHub repo {repo.slug}", file=sys.stderr)
        current = read_state(target, repo)
    else:
        current = load_state(state)
    return Preflight(target, repo, current, push_branch(target, current))


def make_plan(desired: DesiredState, pre: Preflight) -> Plan:
    return plan(desired, pre.current, pre.branch)


def run(
    desired: DesiredState,
    pre: Preflight,
    result: Plan,
    dry_run: bool,
    warn_no_commit: bool = True,
) -> Plan | Report:
    """Return the plan for a dry run, or run it. The commit check is only for a live run."""
    pushing = pre.branch is not None and any(isinstance(op, Push) for op in result.operations)
    no_commit = pushing and pre.branch is not None and not has_commit(pre.target, pre.branch)
    if dry_run or pre.repo is None:
        # The spec's dry run makes no commit, so a missing commit is a warning here.
        if no_commit and warn_no_commit:
            print(
                "bootstrap: warning: the local branch has no commit yet; a live run needs one",
                file=sys.stderr,
            )
        return result
    if no_commit:
        raise BootstrapError(
            "the local branch has no commit, so there is nothing to push. Commit first."
        )
    if pushing:
        push_timeout()  # a bad value stops the command before the first write
    return execute(pre.target, pre.repo, result, desired, pre.current.repository.default_branch)


def github(target: Path, dry_run: bool, state: Path | None) -> Plan | Report:
    """Plan the GitHub operations, and run them unless this is a dry run."""
    if state is not None and not dry_run:
        raise BootstrapError(STATE_NEEDS_DRY_RUN)
    target = resolve_target(target)
    desired = load_desired(target)  # a bad settings file fails before any call to gh
    pre = preflight(target, state)
    return run(desired, pre, make_plan(desired, pre), dry_run)


STATE_NEEDS_DRY_RUN = "a state file only works with --dry-run, so a fake state cannot change GitHub"
NOTE_EXISTING = (
    "bootstrap: existing repo: the GitHub step is skipped. If GitHub has no branch yet, commit the "
    "bootstrap files on your branch, then run `bootstrap github .`, which pushes the branch "
    "itself. Otherwise, merge the bootstrap pull request, then run `bootstrap github .`."
)
NOTE_NO_ORIGIN = (
    "bootstrap: no origin remote: the GitHub step is skipped. "
    "Add origin, then run `bootstrap github .`."
)


def repo_is_empty(target: Path) -> bool:
    """True when HEAD has no commit. A repo with branches and an unborn HEAD is an error."""
    if head_has_commit(target):
        return False
    if has_branches(target):
        raise BootstrapError(
            "HEAD has no commit, but the repo has branches. Check out a branch first."
        )
    return True


def github_preflight(
    target: Path, state: Path | None, empty: bool, dry_run: bool
) -> Preflight | None:
    """Read GitHub (or the state file) before apply writes anything. None: no GitHub step."""
    if not empty or (state is None and not has_origin(target)):
        return None
    pre = preflight(target, state)
    if not pre.current.empty:
        raise BootstrapError(
            "the local repo has no commit, but GitHub already has a branch. "
            "Clone the GitHub repo, and run `bootstrap apply` in the clone."
        )
    if not dry_run:
        push_timeout()  # a bad value stops the command before the first write
    return pre


def written_paths(target: Path, render: Render) -> list[str]:
    """The files that apply writes. Copier does not write a `_skip_if_exists` file that exists."""
    paths = {str(p) for p in render.files if not (p in render.skipped and (target / p).exists())}
    return sorted(paths | {str(ANSWERS_FILE), *AGENT_FILES})


def check_ignored(target: Path, written: list[str], empty: bool) -> list[str]:
    """Stop for an empty repo, which commits these files. Warn for an existing repo."""
    ignored = ignored_paths(target, written)
    if ignored and empty:
        raise BootstrapError(
            "git ignores these files that apply writes: "
            + ", ".join(ignored)
            + ". Change your ignore rules, or add the files by hand."
        )
    if ignored:
        print(
            "bootstrap: warning: git ignores these files that apply writes, so a commit of "
            "the bootstrap changes will leave them out: " + ", ".join(ignored),
            file=sys.stderr,
        )
    return ignored


def agent_texts(worker: copier.Worker, existing: dict[str, str | None]) -> dict[str, str]:
    """The text of each agent file, with the sections of the template."""
    with copier_errors():
        sections_dir = worker.template.local_abspath / "sections"
    # Read every section before Copier writes, so a missing file leaves no half update.
    return {
        filename: markers.render(existing[filename], read_sections(sections_dir, folder, names))
        for filename, (folder, names) in AGENT_FILES.items()
    }


def interrupt_hint(empty: bool, pre: Preflight | None) -> str:
    """How to finish the work when Ctrl-C comes after apply began to write."""
    if not empty:
        return "bootstrap: interrupted. The files may be partly written. Check `git status`."
    then = (
        "Then run `bootstrap github .`."
        if pre is not None
        else ("Then add origin, and run `bootstrap github .`.")
    )
    return (
        "bootstrap: interrupted. Check `git status`. If the files are not committed, run "
        f'`git commit -m "{COMMIT_MESSAGE}"`. {then}'
    )


@contextmanager
def interrupt_note(hint: str | None) -> Iterator[None]:
    """Print the hint when Ctrl-C comes while apply writes. A dry run has no hint."""
    try:
        yield
    except KeyboardInterrupt:
        if hint:
            print(hint, file=sys.stderr)
        raise


def write_files(
    worker: copier.Worker,
    existing: dict[str, str | None],
    stored: dict[str, object] | None,
    target: Path,
    dry_run: bool,
) -> None:
    """Write the files. A dry run reads every input that a live run reads, and writes nothing."""
    with worker:
        texts = agent_texts(worker, existing)
        if dry_run:
            return
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


def settings_of(render: Render) -> DesiredState:
    """The GitHub settings in the render. A live run checks them before it writes anything."""
    text = render.files.get(SETTINGS_PATH)
    if text is None:
        raise BootstrapError(f"the template has no {SETTINGS_PATH}")
    return parse_desired(text.decode("utf-8"), str(SETTINGS_PATH))


def preview(
    render: Render,
    deleted: list[Path],
    pre: Preflight | None,
    written: list[str],
    ignored: list[str],
) -> dict[str, Any]:
    """The output of a dry run: the files, and the GitHub plan when there is one."""
    plan_json: Any = None
    if pre is not None:
        plan_json = json.loads(
            make_plan(settings_of(render), pre).model_dump_json(exclude_none=True)
        )
    return {
        "files": written,
        "deleted": sorted(str(path) for path in deleted),
        "ignored": ignored,
        "plan": plan_json,
    }


def finish_apply(
    target: Path, pre: Preflight | None, desired: DesiredState | None, written: list[str]
) -> Report | None:
    """Make the first commit, then run the GitHub step when there is one."""
    commit_written(target, written, COMMIT_MESSAGE)
    if pre is None or desired is None:
        print(NOTE_NO_ORIGIN, file=sys.stderr)
        return None
    result = run(desired, pre, make_plan(desired, pre), dry_run=False, warn_no_commit=False)
    return result if isinstance(result, Report) else None


def apply_inputs(
    target: Path, template: str, template_version: str | None
) -> tuple[dict[str, str | None], dict[str, object] | None, str, str, list[str]]:
    """Check the agent files, the answers file and the template. Return what the run needs."""
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
    return existing, stored, source, resolve_version(source, template_version), found


def apply(
    target: Path,
    template: str,
    template_version: str | None = None,
    dry_run: bool = False,
    state: Path | None = None,
) -> dict[str, Any] | Report | None:
    """Write the standards. An empty repo also gets the first commit and the GitHub step."""
    if state is not None and not dry_run:
        raise BootstrapError(STATE_NEEDS_DRY_RUN)
    target = resolve_target(target)
    check_clean_repo_root(target)
    empty = repo_is_empty(target)
    existing, stored, source, version, found = apply_inputs(target, template, template_version)
    pre = github_preflight(target, state, empty, dry_run)  # before any write
    render, deleted = check_template_clashes(target, source, version, found, stored)
    written = written_paths(target, render)
    ignored = check_ignored(target, written, empty)
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
    desired = settings_of(render) if pre is not None else None  # before any write
    hint = interrupt_hint(empty, pre)
    with interrupt_note(hint if not dry_run else None):
        write_files(worker, existing, stored, target, dry_run)
    if dry_run:
        if pre is None:
            print(NOTE_EXISTING if not empty else NOTE_NO_ORIGIN, file=sys.stderr)
        return preview(render, deleted, pre, written, ignored)
    if not empty:
        print(NOTE_EXISTING, file=sys.stderr)
        return None
    with interrupt_note(hint):
        return finish_apply(target, pre, desired, written)


def print_apply(outcome: dict[str, Any] | Report | None) -> int:
    """Print what `apply` returned. The exit code is 1 for a failed GitHub step."""
    if isinstance(outcome, dict):
        print(json.dumps(outcome, indent=2))
    elif outcome is not None:
        print(outcome.model_dump_json(indent=2, exclude_none=True))
        return 1 if outcome.failed else 0
    return 0


def interrupted(signum: int, frame: object) -> None:
    raise KeyboardInterrupt


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
    apply_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="write no file and make no commit; print the files and the GitHub plan",
    )
    apply_parser.add_argument(
        "--state", type=Path, help="GitHub state file; it replaces the read of GitHub through gh"
    )
    github_parser = commands.add_parser("github", help="plan the GitHub settings of a repo")
    github_parser.add_argument("target", type=Path)
    github_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the plan as JSON and change nothing on GitHub",
    )
    github_parser.add_argument(
        "--state", type=Path, help="GitHub state file; it replaces the read of GitHub through gh"
    )
    args = parser.parse_args(argv)
    github_run = args.command == "github" and not args.dry_run
    # A hangup or a SIGTERM would kill Python without cleanup, and a push would go on. Treat
    # them as an interrupt, so the code that runs git can kill git and its helpers first.
    for name in ("SIGTERM", "SIGHUP"):
        number = getattr(signal, name)
        # Keep a signal that the user ignored, for example with `nohup`.
        if signal.getsignal(number) is signal.SIG_DFL:
            signal.signal(number, interrupted)
    try:
        if args.command == "github":
            result = github(args.target, args.dry_run, args.state)
            print(result.model_dump_json(indent=2, exclude_none=True))
            return 1 if isinstance(result, Report) and result.failed else 0
        return print_apply(
            apply(args.target, args.template, args.template_version, args.dry_run, args.state)
        )
    except (BootstrapError, CopierError, OSError, UnicodeDecodeError) as error:
        print(f"bootstrap: error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        # The `github` command can have changed GitHub, for example with a push.
        changed = " A change on GitHub, such as a push, may have happened." if github_run else ""
        message = f"bootstrap: interrupted.{changed} Run again to converge."
        # `apply` has printed its own recovery steps, when it had begun to write.
        print("bootstrap: interrupted." if args.command == "apply" else message, file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())

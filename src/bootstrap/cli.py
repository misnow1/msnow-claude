"""The `bootstrap` command."""

import argparse
import sys
from pathlib import Path

import copier

from bootstrap import markers

DEFAULT_TEMPLATE = "https://github.com/misnow1/msnow-claude.git"
AGENT_FILES = {
    "AGENTS.md": ("agents", ["tdd", "libraries", "scripts", "issues", "memory"]),
    "CLAUDE.md": ("claude", ["agents-import"]),
}


class BootstrapError(Exception):
    """A user-facing error. The command prints it and exits with 1."""


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
    text = path.read_text(encoding="utf-8")
    try:
        markers.find_sections(text.splitlines(keepends=True))
    except markers.MarkerError as error:
        raise BootstrapError(f"{path.name}: {error}") from error
    return text


def apply(target: Path, template: str) -> None:
    if not target.is_dir():
        raise BootstrapError(f"target is not an existing directory: {target}")
    existing = {name: read_existing(target / name) for name in AGENT_FILES}
    with copier.Worker(src_path=template, dst_path=target, defaults=True, quiet=True) as worker:
        worker.run_copy()
        sections_dir = worker.template.local_abspath / "sections"
        for filename, (folder, names) in AGENT_FILES.items():
            bodies = {
                name: (sections_dir / folder / f"{name}.md").read_text(encoding="utf-8")
                for name in names
            }
            text = markers.render(existing[filename], bodies)
            markers.write_atomic(target / filename, text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bootstrap")
    commands = parser.add_subparsers(dest="command", required=True)
    apply_parser = commands.add_parser("apply", help="apply the standards to a repo")
    apply_parser.add_argument("target", type=Path)
    apply_parser.add_argument("--template", default=DEFAULT_TEMPLATE)
    args = parser.parse_args(argv)
    try:
        apply(args.target, args.template)
    except (BootstrapError, copier.CopierError) as error:
        print(f"bootstrap: error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

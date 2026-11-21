"""The `bootstrap` command."""

import argparse
import sys
from pathlib import Path

import copier
import yaml
from copier.errors import CopierError

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
    with path.open(encoding="utf-8", newline="") as stream:
        text = stream.read()
    try:
        markers.find_sections(text.splitlines(keepends=True))
    except markers.MarkerError as error:
        raise BootstrapError(f"{path.name}: {error}") from error
    return text


def normalize_source(source: str) -> str:
    """Return a comparable form of a template source: a resolved path or a trimmed URL."""
    path = Path(source)
    if path.exists():
        return str(path.resolve())
    return source.rstrip("/").removesuffix(".git")


def check_answers_file(path: Path, template: str) -> None:
    """Raise BootstrapError when the answers file belongs to another template.

    Copier overwrites the answers file, which would break `copier update` for that template.
    """
    if not path.is_file():
        return
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


def apply(target: Path, template: str) -> None:
    if not target.is_dir():
        raise BootstrapError(f"target is not an existing directory: {target}")
    existing = {name: read_existing(target / name) for name in AGENT_FILES}
    check_answers_file(target / ".copier-answers.yml", template)
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
    except (BootstrapError, CopierError, OSError, UnicodeDecodeError) as error:
        print(f"bootstrap: error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

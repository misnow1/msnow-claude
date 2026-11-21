"""Write marked sections into a Markdown file."""

import os
import re
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

MARKER = re.compile(r"^<!-- bootstrap:(begin|end) ([a-z0-9-]+) -->\s*$")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


class MarkerError(ValueError):
    """The markers in a file are unbalanced, duplicated, nested or out of order."""


def block(name: str, body: str) -> str:
    return f"<!-- bootstrap:begin {name} -->\n{body.strip()}\n<!-- bootstrap:end {name} -->\n"


def marker_lines(lines: list[str]) -> Iterator[tuple[int, str, str]]:
    """Yield the index, kind and name of each marker line outside code fences."""
    fence: str | None = None
    for index, line in enumerate(lines):
        fence_match = FENCE.match(line)
        if fence_match:
            run = fence_match.group(1)
            if fence is None:
                fence = run
            elif run[0] == fence[0] and len(run) >= len(fence):
                fence = None
            continue
        marker = MARKER.match(line)
        if fence is None and marker:
            yield index, marker.group(1), marker.group(2)


def find_sections(lines: list[str]) -> dict[str, tuple[int, int]]:
    """Return the begin and end line index of each marked section outside code fences.

    Raise MarkerError when the markers are not well formed.
    """
    spans: dict[str, tuple[int, int]] = {}
    open_name: str | None = None
    open_line = 0
    for index, kind, name in marker_lines(lines):
        if kind == "begin":
            if open_name is not None:
                raise MarkerError(f"line {index + 1}: section {name} starts inside {open_name}")
            if name in spans:
                raise MarkerError(f"line {index + 1}: section {name} appears twice")
            open_name, open_line = name, index
        elif open_name != name:
            raise MarkerError(f"line {index + 1}: end marker {name} has no matching begin marker")
        else:
            spans[name] = (open_line, index)
            open_name = None
    if open_name is not None:
        raise MarkerError(f"line {open_line + 1}: section {open_name} has no end marker")
    return spans


def render(existing: str | None, sections: dict[str, str]) -> str:
    """Return the file text with each section written. Text outside the markers stays."""
    if existing is None:
        return "\n".join(block(name, body) for name, body in sections.items())
    lines = existing.splitlines(keepends=True)
    spans = find_sections(lines)
    for name, (begin, end) in sorted(spans.items(), key=lambda item: -item[1][0]):
        if name in sections:
            lines[begin : end + 1] = [block(name, sections[name])]
    text = "".join(lines)
    for name, body in sections.items():
        if name not in spans:
            if text and not text.endswith("\n"):
                text += "\n"
            text += ("\n" if text else "") + block(name, body)
    return text


def write_atomic(path: Path, text: str) -> None:
    """Write text to a temp file in the same directory, then rename it over the path."""
    handle, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    temp = Path(temp_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        if path.exists():
            shutil.copymode(path, temp)
        else:
            temp.chmod(0o644)
        os.replace(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise

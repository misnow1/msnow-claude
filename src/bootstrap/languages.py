"""Detect the languages of a target repo from the files in its root."""

from pathlib import Path

from bootstrap.errors import BootstrapError

MARKERS = {"python": "pyproject.toml", "go": "go.mod", "rust": "Cargo.toml"}


def detect(target: Path) -> list[str]:
    """Return the languages whose marker file is in the root of target."""
    return sorted(name for name, marker in MARKERS.items() if (target / marker).is_file())


def saved(answers: dict[str, object] | None) -> list[str]:
    """Return the languages saved in the answers file.

    Raise BootstrapError when the saved value is not a list of known languages.
    """
    if answers is None or "languages" not in answers:
        return []
    value = answers["languages"]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise BootstrapError(f".copier-answers.yml: languages must be a list, not {value!r}")
    unknown = sorted(set(value) - set(MARKERS))
    if unknown:
        raise BootstrapError(
            f".copier-answers.yml: unknown languages {', '.join(unknown)}. "
            f"Known languages: {', '.join(sorted(MARKERS))}"
        )
    return value

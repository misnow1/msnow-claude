"""Find the template versions. Each version is a git tag in the template source."""

import os
import subprocess

from packaging.version import InvalidVersion, Version

from bootstrap.errors import BootstrapError


def list_tags(source: str) -> list[str]:
    """Return the tags of a template source that are valid versions."""
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--tags", "--refs", "--", source],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except subprocess.CalledProcessError as error:
        raise BootstrapError(f"cannot list the tags of {source}: {error.stderr.strip()}") from error
    tags = []
    for line in result.stdout.splitlines():
        tag = line.split("\t", 1)[1].removeprefix("refs/tags/")
        try:
            Version(tag)
        except InvalidVersion:
            continue
        tags.append(tag)
    return tags


def resolve_version(source: str, requested: str | None = None) -> str:
    """Return the requested version, or the highest release tag of a template source.

    A requested version must be a version tag of the source. A prerelease tag is allowed
    when it is requested by name.
    """
    tags = list_tags(source)
    releases = [tag for tag in tags if not Version(tag).is_prerelease]
    if requested is None and not releases:
        raise BootstrapError(f"the template {source} has no release tag")
    if requested is None:
        return max(releases, key=Version)
    if not tags:
        raise BootstrapError(f"the template {source} has no version tag, so {requested} is unknown")
    if requested not in tags:
        raise BootstrapError(
            f"{requested} is not a version tag of {source}. Version tags: {', '.join(tags)}"
        )
    return requested

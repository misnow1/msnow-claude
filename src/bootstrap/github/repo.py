"""Find the GitHub repo of a target from its `origin` remote.

The command never lets `gh` pick the repo from the placeholders `{owner}` and `{repo}`. `gh`
prefers a remote named `upstream`, a default that `gh repo set-default` sets, and `GH_REPO`.
So a fork clone would change the upstream repo while `git push` goes to `origin`.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from bootstrap.errors import BootstrapError
from bootstrap.gitutil import run_git

HOST = "github.com"
OWNER = re.compile(r"[A-Za-z0-9-]+")
NAME = re.compile(r"[A-Za-z0-9._-]+")
SCP_LIKE = re.compile(r"^(?:[^@/:]+@)?([^:/]+):(?!//)(.+)$")


@dataclass(frozen=True)
class Repo:
    owner: str
    name: str

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.name}"


def parse_remote(url: str) -> Repo:
    """Read the owner and name from a remote URL.

    No message holds a part of the URL, because the URL can hold a token.
    """
    if "://" in url:
        try:
            parsed = urlparse(url)
            scheme, host, path = parsed.scheme.lower(), parsed.hostname or "", parsed.path
        except ValueError:
            raise BootstrapError("the URL of origin is not valid") from None
        # `file://`, `git://` and `http://` can reach another place than github.com, or send a
        # token in clear text.
        if scheme not in {"https", "ssh"}:
            raise BootstrapError("the URL of origin must use https or ssh")
    else:
        found = SCP_LIKE.match(url)
        if found is None:
            raise BootstrapError("the URL of origin is not a GitHub URL")
        host, path = found.group(1).lower(), found.group(2)
    if host != HOST:
        raise BootstrapError(
            f"origin is not on {HOST}. For an SSH host alias, use the https URL, or a "
            f"'Host {HOST}' entry in ~/.ssh/config in place of the alias."
        )
    parts = path.strip("/").removesuffix(".git").split("/")
    if (
        len(parts) != 2
        or not OWNER.fullmatch(parts[0])
        or not NAME.fullmatch(parts[1])
        or parts[1] in {".", ".."}
    ):
        raise BootstrapError("the path of origin is not OWNER/REPO")
    return Repo(parts[0], parts[1])


def remote_url(target: Path, *flags: str) -> str:
    """The one URL of origin. A second URL is a second place that `git push` reaches."""
    try:
        names = run_git(target, "remote").split()
        if "origin" not in names:
            raise BootstrapError("the repo has no origin remote, so the GitHub repo is unknown")
        out = run_git(target, "remote", "get-url", "--all", *flags, "origin")
    except BootstrapError as error:
        if "no origin remote" in str(error):
            raise
        raise BootstrapError(f"cannot read the remotes of {target}. Is it a git repo?") from None
    urls = [line for line in out.splitlines() if line.strip()]
    if len(urls) != 1:
        raise BootstrapError("origin has more than one URL, and git push uses all of them")
    return urls[0].strip()


def resolve_repo(target: Path) -> Repo:
    """The repo that origin names. The fetch and the push URL must name the same repo.

    The push URL can differ (`pushurl`, `pushInsteadOf`). The command reads and changes the repo
    that it finds here, and `git push` must reach that same repo.
    """
    fetched = parse_remote(remote_url(target))
    pushed = parse_remote(remote_url(target, "--push"))
    if fetched != pushed:
        raise BootstrapError(
            f"the fetch and push URLs of origin name different repos ({fetched.slug} and "
            f"{pushed.slug})"
        )
    return fetched

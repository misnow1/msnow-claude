"""Run `gh api` and turn its failures into one error type."""

import contextlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from bootstrap.github.repo import Repo

TIMEOUT_SECONDS = 60
# These make `gh` print the HTTP exchange, or color its output. The exchange can hold the token.
DROPPED_ENV = ("GH_DEBUG", "DEBUG", "GODEBUG", "GH_FORCE_TTY", "CLICOLOR_FORCE")


class GhError(Exception):
    """A failed `gh` call. `status` is the HTTP status, or None when there was no response."""

    def __init__(self, status: int | None, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.detail = detail or message


def environment(repo: Repo) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in DROPPED_ENV}
    env.update(GH_REPO=repo.slug, GH_HOST="github.com", GH_PROMPT_DISABLED="1")
    return env


def parse_error(stdout: str, stderr: str) -> GhError:
    """Read the error of `gh`: the JSON body on stdout, and the `gh:` line on stderr."""
    if "unknown flag: --slurp" in stderr:
        return GhError(None, "gh 2.48 or later is needed: this gh does not know --slurp")
    body: Any = None
    with contextlib.suppress(ValueError):
        body = json.loads(stdout)
    if isinstance(body, list) and len(body) == 1:  # a paginated call wraps the error body
        body = body[0]
    body = body if isinstance(body, dict) else {}
    lines = [
        line.removeprefix("gh:").strip() for line in stderr.splitlines() if line.startswith("gh:")
    ]
    last = [line.strip() for line in stderr.splitlines() if line.strip()]
    message = str(
        body.get("message") or (lines[-1] if lines else last[-1] if last else "gh failed")
    )
    status = body.get("status")
    found = re.search(r"\(HTTP (\d{3})\)", stderr)
    code = int(status) if str(status).isdigit() else int(found[1]) if found else None
    errors = body.get("errors") if isinstance(body.get("errors"), list) else []
    extra = [str(e.get("message") or e.get("code")) for e in errors if isinstance(e, dict)]
    return GhError(code, message, "; ".join([message, *extra]))


def api(target: Path, repo: Repo, endpoint: str, paginate: bool = False) -> Any:
    """Call a GET endpoint. A paginated call returns the items of all pages in one list."""
    command = ["gh", "api", endpoint, *(["--paginate", "--slurp"] if paginate else [])]
    try:
        result = subprocess.run(
            command,
            cwd=target,
            env=environment(repo),
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError:
        raise GhError(None, "gh is not installed, or it is not on PATH") from None
    except subprocess.TimeoutExpired:
        raise GhError(None, f"gh timed out after {TIMEOUT_SECONDS} s") from None
    if result.returncode != 0:
        raise parse_error(result.stdout, result.stderr)
    try:
        data = json.loads(result.stdout) if result.stdout.strip() else None
    except ValueError:
        raise GhError(None, "gh did not return JSON") from None
    if paginate and isinstance(data, list):
        return [item for page in data for item in (page if isinstance(page, list) else [page])]
    return data

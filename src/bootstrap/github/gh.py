"""Run `gh api` and turn its failures into one error type."""

import contextlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from bootstrap.github.repo import Repo
from bootstrap.gitutil import env_seconds

DEFAULT_TIMEOUT_SECONDS = 60
WRITE_METHODS = {"PATCH", "POST", "PUT"}
# These make `gh` print the HTTP exchange, or color its output. The exchange can hold the token.
DROPPED_ENV = ("GH_DEBUG", "DEBUG", "GODEBUG", "GH_FORCE_TTY", "CLICOLOR_FORCE")


class GhError(Exception):
    """A failed `gh` call. `status` is the HTTP status, or None when there was no response."""

    def __init__(
        self, status: int | None, message: str, detail: str = "", result_unknown: bool = False
    ) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.detail = detail or message
        # A write that timed out, or that got a 5xx, may have applied.
        self.result_unknown = result_unknown


def timeout_seconds() -> int:
    """`BOOTSTRAP_GH_TIMEOUT` seconds, 60 by default."""
    return env_seconds("BOOTSTRAP_GH_TIMEOUT", DEFAULT_TIMEOUT_SECONDS)


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
    extra = [
        str(e.get("message") or e.get("code")) if isinstance(e, dict) else str(e) for e in errors
    ]
    return GhError(code, message, "; ".join([message, *extra]))


def check_write(repo: Repo, endpoint: str, method: str) -> None:
    """A write goes to the repo and nowhere else. No code path writes to `orgs/` or another repo."""
    base = f"repos/{repo.slug}"
    under_repo = endpoint == base or endpoint.startswith(f"{base}/")
    odd = any(part in endpoint for part in ("..", "?", "#", "//"))
    if method not in WRITE_METHODS or not under_repo or odd:
        raise ValueError(f"refusing a {method} request to {endpoint}")


def api(
    target: Path,
    repo: Repo,
    endpoint: str,
    paginate: bool = False,
    method: str = "GET",
    body: Any = None,
) -> Any:
    """Call an endpoint. A paginated call returns the items of all pages in one list."""
    write = method != "GET"
    if write:
        check_write(repo, endpoint, method)
    timeout = timeout_seconds()
    command = ["gh", "api", endpoint, *(["--paginate", "--slurp"] if paginate else [])]
    if write:
        command += ["--method", method, "--input", "-"]
    try:
        result = subprocess.run(
            command,
            cwd=target,
            env=environment(repo),
            capture_output=True,
            text=True,
            input=json.dumps(body) if write else None,
            stdin=None if write else subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        raise GhError(None, "gh is not installed, or it is not on PATH") from None
    except subprocess.TimeoutExpired:
        unknown = "; the result is unknown" if write else ""
        raise GhError(
            None, f"gh timed out after {timeout} s{unknown}", result_unknown=write
        ) from None
    if result.returncode != 0:
        error = parse_error(result.stdout, result.stderr)
        if write and error.status is not None and error.status >= 500:
            error.message += "; the result is unknown"
            error.detail += "; the result is unknown"
            error.result_unknown = True
        raise error
    try:
        data = json.loads(result.stdout) if result.stdout.strip() else None
    except ValueError:
        unknown = "; the result is unknown" if write else ""
        raise GhError(None, f"gh did not return JSON{unknown}", result_unknown=write) from None
    if write and data is None:
        raise GhError(None, "gh returned no data; the result is unknown", result_unknown=True)
    if paginate and isinstance(data, list):
        return [item for page in data for item in (page if isinstance(page, list) else [page])]
    return data

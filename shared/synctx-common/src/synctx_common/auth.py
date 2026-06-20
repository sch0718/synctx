"""Common authentication helpers."""

from __future__ import annotations

import base64
import os
from pathlib import Path


def read_secret(*, credential_env: str | None = None, credential_file: str | None = None) -> str:
    """Read a secret from env or file. Env takes precedence."""
    if credential_env:
        value = (os.environ.get(credential_env) or "").strip()
        if value:
            return value
    if credential_file:
        return Path(credential_file).read_text(encoding="utf-8").strip()
    return ""


def basic_auth_header(username: str, password: str) -> str:
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return f"Authorization: Basic {token}"


def git_token_auth_header(token: str) -> str:
    # GitHub rejects Bearer for git-over-HTTPS. Basic is broadly supported by Git hosts.
    return basic_auth_header("x-access-token", token)

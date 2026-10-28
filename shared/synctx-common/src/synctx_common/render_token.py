from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
from collections.abc import Mapping
import re
import secrets
import time

from .secret_keys import SecretKeyError, load_secret_key


_TOKEN_RE = re.compile(r"^v1\.([1-9][0-9]{0,10})\.([A-Za-z0-9_-]{2,340})\.([0-9a-f]{40})\.([A-Za-z0-9_-]{22})$")
_SHA_RE = re.compile(r"[0-9a-f]{40}")
_REF_FORBIDDEN_RE = re.compile(r"[\x00-\x20\x7f-\x9f~^:?*\[\\|]")
_RANDOM_KEY: bytes | None = None


class RenderTokenError(ValueError):
    pass


def load_render_keys(environ: Mapping[str, str] | None = None) -> tuple[bytes, bytes | None]:
    global _RANDOM_KEY
    env = os.environ if environ is None else environ

    def load(name: str) -> bytes | None:
        try:
            return load_secret_key(name, env)
        except SecretKeyError as exc:
            raise RenderTokenError(str(exc)) from exc

    key = load("SYNCTX_RENDER_SECRET")
    previous_key = load("SYNCTX_RENDER_SECRET_PREVIOUS")
    if key is None:
        if previous_key is not None:
            raise RenderTokenError("이전 렌더 키만 지정할 수 없습니다")
        if _RANDOM_KEY is None:
            _RANDOM_KEY = secrets.token_bytes(32)
        key = _RANDOM_KEY
    return key, previous_key


def _validate_ref(ref: str) -> bytes:
    try:
        encoded = ref.encode("utf-8")
    except UnicodeError as exc:
        raise RenderTokenError("브랜치명은 UTF-8이어야 합니다") from exc
    if (
        not 1 <= len(encoded) <= 255
        or ref == "HEAD"
        or ref.startswith("-")
        or ref.endswith(".")
        or ".." in ref
        or "@{" in ref
        or _REF_FORBIDDEN_RE.search(ref)
        or any(not part or part.startswith(".") or part.endswith(".lock") for part in ref.split("/"))
    ):
        raise RenderTokenError("유효하지 않은 브랜치명입니다")
    return encoded


def _validate_repo_id(repo_id: str) -> None:
    if "|" in repo_id:
        raise RenderTokenError("저장소 ID에 |를 쓸 수 없습니다")
    try:
        repo_id.encode("utf-8")
    except UnicodeError as exc:
        raise RenderTokenError("저장소 ID는 UTF-8이어야 합니다") from exc


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    try:
        decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RenderTokenError("유효하지 않은 base64url입니다") from exc
    if _encode(decoded) != value:
        raise RenderTokenError("정규형 base64url이 아닙니다")
    return decoded


def _signature(repo_id: str, ref: str, sha: str, exp: str, key: bytes) -> bytes:
    message = f"render|v1|{repo_id}|{ref}|{sha}|{exp}".encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).digest()[:16]


def issue_render_token(
    repo_id: str, ref: str, sha: str, *, key: bytes, ttl_seconds: int = 14400, now: int | None = None,
) -> str:
    _validate_repo_id(repo_id)
    encoded_ref = _validate_ref(ref)
    if _SHA_RE.fullmatch(sha) is None:
        raise RenderTokenError("커밋 SHA는 소문자 16진수 40자여야 합니다")
    if not isinstance(ttl_seconds, int) or isinstance(ttl_seconds, bool) or ttl_seconds <= 0:
        raise RenderTokenError("토큰 TTL은 양의 정수여야 합니다")
    exp = str((int(time.time()) if now is None else now) + ttl_seconds)
    if re.fullmatch(r"[1-9][0-9]{0,10}", exp) is None:
        raise RenderTokenError("유효하지 않은 만료 시각입니다")
    signature = _signature(repo_id, ref, sha, exp, key)
    return f"v1.{exp}.{_encode(encoded_ref)}.{sha}.{_encode(signature)}"


def verify_render_token(
    token: str, repo_id: str, *, key: bytes, previous_key: bytes | None = None, now: int | None = None,
) -> tuple[str, str]:
    _validate_repo_id(repo_id)
    match = _TOKEN_RE.fullmatch(token)
    if match is None:
        raise RenderTokenError("유효하지 않은 렌더 토큰 형식입니다")
    exp, encoded_ref, sha, encoded_signature = match.groups()
    try:
        ref = _decode(encoded_ref).decode("utf-8")
    except UnicodeError as exc:
        raise RenderTokenError("브랜치명은 UTF-8이어야 합니다") from exc
    _validate_ref(ref)
    signature = _decode(encoded_signature)
    if len(signature) != 16:
        raise RenderTokenError("서명은 16바이트여야 합니다")
    if (int(time.time()) if now is None else now) >= int(exp):
        raise RenderTokenError("렌더 토큰이 만료되었습니다")
    candidates = [key]
    if previous_key is not None:
        candidates.append(previous_key)
    for candidate in candidates:
        expected = _signature(repo_id, ref, sha, exp, candidate)
        if hmac.compare_digest(expected, signature):
            return ref, sha
    raise RenderTokenError("렌더 토큰 서명이 일치하지 않습니다")

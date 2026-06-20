"""Outbound feedback redaction and allowlist filtering."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from datetime import datetime, timezone
from typing import Any

from synctx_common.config import SynctxConfig


def _stable_event_id(work_package: dict[str, Any]) -> str:
    raw = f"{work_package.get('id')}:{work_package.get('updatedAt') or work_package.get('updated_at') or ''}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"op_{work_package.get('id', 'unknown')}_{digest}"


def _hash_user(user_id: str, salt: str) -> str:
    return hmac.new(salt.encode("utf-8"), str(user_id).encode("utf-8"), hashlib.sha256).hexdigest()[:16]


def _extract_external_key(config: SynctxConfig, work_package: dict[str, Any]) -> str | None:
    op_config = config.targets.openproject
    if not op_config:
        return None
    field = op_config.external_key_custom_field
    if field in work_package:
        return work_package[field]
    custom_fields = work_package.get("customFields")
    if isinstance(custom_fields, dict):
        value = custom_fields.get(field)
        if isinstance(value, dict):
            return value.get("value") or value.get("raw")
        if isinstance(value, str):
            return value
    description = work_package.get("description")
    if isinstance(description, dict):
        raw = description.get("raw") or ""
    else:
        raw = str(description or "")
    match = re.search(r"synctx_external_key\s*[:=]\s*([^\s]+)", raw)
    return match.group(1) if match else None


def redact_work_package(config: SynctxConfig, work_package: dict[str, Any]) -> dict[str, Any] | None:
    external_key = _extract_external_key(config, work_package)
    if not external_key:
        return None

    allowed_fields = set(config.outbound_feedback.allowed_fields)
    changes: dict[str, Any] = {}
    ignored_fields: list[str] = []
    for field, value in work_package.items():
        if field in allowed_fields:
            changes[field] = value
        elif field not in {"id", "updatedAt", "_links", "author"}:
            ignored_fields.append(field)

    payload = {
        "event_id": _stable_event_id(work_package),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "openproject",
        "work_package_id": work_package["id"],
        "external_key": external_key,
        "changes": changes,
        "ignored_fields": sorted(ignored_fields),
    }

    salt = os.getenv("OUTBOUND_FEEDBACK_HASH_SALT")
    if not salt:
        raise ValueError("OUTBOUND_FEEDBACK_HASH_SALT must be set")
    author = work_package.get("author")
    if isinstance(author, dict):
        payload["changed_by_hash"] = _hash_user(str(author.get("id", "unknown")), salt)
    return payload

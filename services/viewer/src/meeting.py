"""Viewer 회의 모드: 모드 판단, 설정 JSON, 템플릿 조각."""

from __future__ import annotations

import html
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

from synctx_common.config import RepoProfile
from synctx_common.frontmatter import MeetingFrontMatter, validate_meeting

ROOT_SELECTOR = "article.markdown-body"
STATIC_DIR = Path(__file__).resolve().parent / "static" / "meeting"
EMBED_MARKER = "<!--synctx-meeting-embed-->"

_MARKDOWN_SUFFIXES = (".md", ".markdown")
_BACKSLASH = chr(92)
_SCRIPT_UNSAFE = ("<", ">", "&", chr(0x2028), chr(0x2029))
_NOTICE_STYLE = (
    "margin:16px 45px 0;padding:12px 16px;border:1px solid var(--border-color,#d0d7de);"
    "border-left:4px solid var(--primary,#0969da);background:var(--bg-neutral,#f6f8fa);"
    "color:var(--text-primary,#1f2328);border-radius:6px;font-size:14px"
)


@dataclass
class MeetingContext:
    enabled: bool
    reason: Optional[str]
    meeting: Optional[MeetingFrontMatter]


def resolve_meeting_context(repo: RepoProfile, path: str, front_matter: dict, ref: Optional[str]) -> MeetingContext:
    synctx_meta = front_matter.get("synctx") if isinstance(front_matter, dict) else None
    if not path.lower().endswith(_MARKDOWN_SUFFIXES) or not isinstance(synctx_meta, dict) or synctx_meta.get("type") != "meeting":
        return MeetingContext(False, None, None)
    meeting, errors = validate_meeting(front_matter)
    if errors:
        return MeetingContext(False, f"회의 정보에 오류가 있습니다: {errors[0]}", None)
    if not repo.collab.enabled:
        return MeetingContext(False, "이 저장소는 회의 연동 대상이 아닙니다", meeting)
    if ref and ref != repo.default_ref:
        return MeetingContext(False, "회의 모드는 기본 브랜치에서만 사용할 수 있습니다", meeting)
    return MeetingContext(True, None, meeting)


def storage_key(repo_id: str, meeting_id: str) -> str:
    return f"synctx-meeting:{repo_id}:{meeting_id}:v1"


def export_url(view_prefix: str, repo_id: str, path: str) -> str:
    return f"{view_prefix}/api/repos/{quote(repo_id)}/meeting-export?path={quote(path)}"


def build_config(repo_id: str, path: str, meeting: MeetingFrontMatter, *, readonly: bool, export_url: Optional[str]) -> dict:
    return {
        "schema": 1,
        "repo_id": repo_id,
        "meeting": {"id": meeting.id, "title": meeting.title, "starts_at": meeting.starts_at.isoformat()},
        "path": path,
        "storage_key": storage_key(repo_id, meeting.id),
        "root_selector": ROOT_SELECTOR,
        "export_url": export_url,
        "readonly": readonly,
    }


def encode_for_script(value: Any) -> str:
    """<script> 안에 넣을 JSON. 설계 3.4.1: <, >, &, U+2028, U+2029를 6글자 유니코드 이스케이프로 바꾼다."""
    text = json.dumps(value, ensure_ascii=False)
    for ch in _SCRIPT_UNSAFE:
        text = text.replace(ch, f"{_BACKSLASH}u{ord(ch):04x}")
    return text


def render_assets(view_prefix: str, config: dict) -> tuple[str, str]:
    head = f'<link rel="stylesheet" href="{view_prefix}/static/meeting/meeting.css">'
    body = (
        f'<script type="application/json" id="meeting-config">{encode_for_script(config)}</script>\n'
        f'    <script src="{view_prefix}/static/meeting/memo-core.js"></script>\n'
        f'    <script src="{view_prefix}/static/meeting/meeting.js"></script>'
    )
    return head, body


def render_notice(ctx: MeetingContext) -> str:
    if not ctx.reason:
        return ""
    return f'<div class="meeting-notice" role="status" style="{_NOTICE_STYLE}">{html.escape(ctx.reason)}</div>'

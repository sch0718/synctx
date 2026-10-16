"""Viewer 화면의 공통 틀(상단 바, 페이지 뼈대, 서랍 컨트롤, 오류 본문)과 화면 문구."""

from __future__ import annotations

import html
from datetime import datetime, timezone
from urllib.parse import quote

from meeting import encode_for_script
from renderer import THEME_VARS_CSS

THEME_LABELS = {
    "github-light": "GitHub 밝게",
    "github-dark": "GitHub 어둡게",
    "cayman": "Cayman",
    "minimal": "Minimal",
    "hacker": "Hacker",
    "modernist": "Modernist",
    "corporate": "Corporate",
}

STATUS_LABELS = {"cloning": "저장소 받는 중", "error": "동기화 실패"}

ERROR_COPY = {
    "repo_missing": ("이 저장소를 찾을 수 없습니다", "주소의 저장소 이름을 확인하세요."),
    "file_missing": ("이 파일을 찾을 수 없습니다", "이름이 바뀌었거나 다른 브랜치에만 있을 수 있습니다."),
    "too_large": ("화면에 보이기에 너무 큰 파일입니다", "원본을 내려받아 여세요."),
    "unsupported": ("화면에서 볼 수 없는 형식입니다", "원본을 내려받아 여세요."),
    "failed": ("문서를 불러오지 못했습니다", "잠시 후 다시 시도하세요. 계속되면 관리자에게 알리세요."),
}

_LOGO = ('<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" '
         'aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M12 2v4m0 12v4M2 12h4m12 0h4"/></svg>')
_MENU = ('<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" '
         'aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h16"/></svg>')
_SEARCH = ('<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" '
           'aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>')

_THEME_BOOT_QUERY = (
    "<script>(function(){var u=new URL(window.location);if(u.searchParams.has('theme'))return;"
    "var s=null;try{s=localStorage.getItem('synctx-theme');}catch(e){}"
    "if(s){u.searchParams.set('theme',s);window.location.replace(u.toString());}})();</script>"
)
_THEME_BOOT_ATTR = (
    "<script>(function(){try{var s=localStorage.getItem('synctx-theme');"
    "if(s)document.documentElement.setAttribute('data-theme',s);}catch(e){}})();</script>"
)

_SHORTCUTS_DIALOG = """<dialog id="shortcuts-dialog" aria-labelledby="shortcuts-title">
  <h2 id="shortcuts-title">단축키</h2>
  <dl class="keys">
    <dt><kbd>⌘</kbd> <kbd>K</kbd> · <kbd>Ctrl</kbd> <kbd>K</kbd> · <kbd>/</kbd></dt><dd>파일 찾기</dd>
    <dt><kbd>t</kbd></dt><dd>이 문서 목차 열기</dd>
    <dt><kbd>?</kbd></dt><dd>이 안내 열기</dd>
    <dt><kbd>Esc</kbd></dt><dd>열린 창·메뉴 닫기</dd>
  </dl>
  <form method="dialog" class="dialog-actions"><button class="btn">닫기</button></form>
</dialog>"""


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def theme_options(current: str) -> str:
    return "".join(
        f'<option value="{esc(key)}"{" selected" if key == current else ""}>{esc(label)}</option>'
        for key, label in THEME_LABELS.items()
    )


def repo_options(repos, active_id: str | None) -> str:
    items = [f'<option value=""{"" if active_id else " selected"}>저장소 선택</option>']
    for r in repos:
        sel = " selected" if r.id == active_id else ""
        items.append(f'<option value="{esc(r.id)}"{sel}>{esc(r.name)}</option>')
    return "".join(items)


def branch_options(branches, active_ref: str | None, default_ref: str | None) -> str:
    items = []
    for b in branches:
        label = f"{b} (기본)" if b == default_ref else b
        sel = " selected" if b == active_ref else ""
        items.append(f'<option value="{esc(b)}"{sel}>{esc(label)}</option>')
    return "".join(items)


def relative_time_ko(iso, now: datetime | None = None) -> str:
    try:
        moment = datetime.fromisoformat(str(iso)) if iso else None
    except ValueError:
        moment = None
    if moment is None:
        return ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    seconds = max(0, int(((now or datetime.now(timezone.utc)) - moment).total_seconds()))
    if seconds < 60:
        return "방금"
    if seconds < 3600:
        return f"{seconds // 60}분 전"
    if seconds < 86400:
        return f"{seconds // 3600}시간 전"
    days = seconds // 86400
    if days < 30:
        return f"{days}일 전"
    if days < 365:
        return f"{days // 30}개월 전"
    return f"{days // 365}년 전"


def _select(label: str, action: str, options: str) -> str:
    return (f'<label class="shell-field"><span class="shell-field-label">{label}</span>'
            f'<select data-action="{action}">{options}</select></label>')


def render_header(*, view_prefix, repos, theme, active_repo=None, branches=None, active_ref=None,
                  default_ref=None, repo_status=None) -> str:
    menu = (f'<button type="button" class="icon-btn shell-menu" data-action="toggle-nav" '
            f'aria-controls="shell-nav" aria-expanded="false" aria-label="메뉴 열기">{_MENU}</button>')
    branch = ""
    if active_repo and branches:
        branch = _select("브랜치", "branch", branch_options(branches, active_ref or default_ref, default_ref))
    status = ""
    if repo_status in STATUS_LABELS:
        status = (f'<span class="shell-status shell-status-{esc(repo_status)}" role="status">'
                  f'{STATUS_LABELS[repo_status]}</span>')
    return f"""<a class="skip-link" href="#main">본문으로 건너뛰기</a>
<header class="shell-header">
  {menu}
  <a class="shell-logo" href="{esc(view_prefix)}/">{_LOGO}<span>synctx</span></a>
  <div class="shell-controls">{_select("저장소", "repo", repo_options(repos, active_repo))}{branch}</div>
  <div class="finder">
    <label class="finder-box">{_SEARCH}<input type="search" id="finder-input" role="combobox" aria-expanded="false" aria-controls="finder-results" aria-autocomplete="list" autocomplete="off" placeholder="파일 찾기" aria-label="파일 찾기"><kbd aria-hidden="true">⌘K</kbd></label>
    <ul id="finder-results" class="finder-results" role="listbox" aria-label="찾은 파일" hidden></ul>
  </div>
  <span class="shell-spacer"></span>
  {status}
  <div class="shell-controls">{_select("테마", "theme", theme_options(theme))}</div>
  <button type="button" class="icon-btn" data-action="shortcuts" aria-label="단축키 안내">?</button>
</header>"""


def render_nav_controls(*, repos, theme, active_repo, branches, active_ref, default_ref) -> str:
    branch = ""
    if active_repo and branches:
        branch = _select("브랜치", "branch", branch_options(branches, active_ref or default_ref, default_ref))
    return (f'<div class="nav-controls">{_select("저장소", "repo", repo_options(repos, active_repo))}'
            f'{branch}{_select("테마", "theme", theme_options(theme))}</div>')


NAV_CLOSE = '<button type="button" class="btn nav-close" data-action="close-nav">메뉴 닫기</button>'


def render_mobile_nav(*, controls: str, extra: str = "") -> str:
    return (f'<nav id="shell-nav" class="shell-nav shell-nav-mobile-only" aria-label="메뉴">'
            f'{NAV_CLOSE}{controls}{extra}</nav>'
            '<div class="nav-backdrop" data-action="close-nav" hidden></div>')


def page(*, view_prefix, title, theme, header, body, repo_id="", ref="", page_class="",
         theme_via_query=False, doc_config=None, head_extra="", body_extra="") -> str:
    classes = page_class
    mode = "query" if theme_via_query else "attr"
    boot = _THEME_BOOT_QUERY if theme_via_query else _THEME_BOOT_ATTR
    vp = esc(view_prefix)
    return f"""<!DOCTYPE html>
<html lang="ko" data-theme="{esc(theme)}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{esc(title)} - synctx</title>
{boot}
<style>{THEME_VARS_CSS}</style>
{head_extra}
<link rel="stylesheet" href="{vp}/static/viewer.css">
</head>
<body class="{esc(classes)}" data-theme-mode="{mode}">
{header}
{body}
<div class="toast-region" id="toast-region"></div>
{_SHORTCUTS_DIALOG}
<script>
var VIEW_PREFIX = {encode_for_script(view_prefix)};
var ACTIVE_REPO = {encode_for_script(repo_id)};
var ACTIVE_REF = {encode_for_script(ref)};
var SYNCTX_DOC = {encode_for_script(doc_config)};
</script>
<script src="{vp}/static/viewer-core.js"></script>
<script src="{vp}/static/viewer.js"></script>
{body_extra}
</body>
</html>"""


def _doc_href(view_prefix, repo_id, path, ref) -> str:
    query = f"?ref={quote(ref, safe='')}" if ref else ""
    return f"{view_prefix}/{quote(repo_id, safe='')}/{quote(path)}{query}"


def error_body(kind, *, view_prefix, repo_id=None, path=None, ref=None, suggestions=(), download_href=None) -> str:
    title, guide = ERROR_COPY[kind]
    parts = [f'<main id="main" class="error-page" tabindex="-1">',
             f'<p class="error-kicker">문제가 생겼습니다</p>',
             f"<h1>{title}</h1>",
             f"<p>{guide}</p>"]
    if path:
        parts.append(f'<p class="error-path">{esc(path)}</p>')
    if suggestions and repo_id:
        links = "".join(
            f'<li><a href="{esc(_doc_href(view_prefix, repo_id, s, ref))}">{esc(s)}</a></li>' for s in suggestions
        )
        parts.append(f'<h2 class="nav-title">비슷한 이름</h2><ul class="suggestions">{links}</ul>')
    actions = []
    if download_href and kind in ("too_large", "unsupported"):
        actions.append(f'<a class="btn btn-primary" href="{esc(download_href)}">원본 내려받기</a>')
    if repo_id and kind != "repo_missing":
        repo_href = f"{view_prefix}/{quote(repo_id, safe='')}" + (f"?ref={quote(ref, safe='')}" if ref else "")
        actions.append(f'<a class="btn" href="{esc(repo_href)}">저장소 처음으로</a>')
    actions.append(f'<a class="btn" href="{esc(view_prefix)}/">저장소 목록</a>')
    if kind == "failed":
        actions.append('<button type="button" class="btn" data-action="reload">다시 시도</button>')
    parts.append(f'<div class="error-actions">{"".join(actions)}</div></main>')
    return "".join(parts)

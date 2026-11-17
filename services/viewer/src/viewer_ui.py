"""Viewer 화면의 공통 틀(상단 바, 페이지 뼈대, 서랍 컨트롤, 오류 본문)과 화면 문구."""

from __future__ import annotations

import html
from datetime import datetime, timezone
from collections.abc import Sequence
from urllib.parse import quote

from meeting import encode_for_script
from meeting_card import KST, time_range_label
from meeting_index import STATUSES, action_items, status_of
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
SEARCH_QUERY_HINT = "두 글자 이상, 100글자 이하로 한 줄만 입력하세요"

SEARCH_STATUS_COPY = {
    "timeout": "3초 안에 찾지 못해 결과를 보여 주지 못했습니다",
    "unsupported": "이 저장소는 부분 복제라 본문 검색을 하지 않습니다",
    "unavailable": "저장소를 받는 중이거나 동기화에 실패해 찾지 못했습니다",
    "failed": "이 저장소를 찾지 못했습니다",
    "truncated": "결과가 많아 일부만 보여 줍니다",
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
_NAV_WIDTH_BOOT = (
    "<script>(function(){try{var v=Number(localStorage.getItem('synctx-nav-width'));"
    "if(isFinite(v)&&v>=200&&v<=600)document.documentElement.style.setProperty('--shell-nav-w',v+'px');"
    "}catch(e){}})();</script>"
)
NAV_RESIZER = (
    '<div class="nav-resizer" role="separator" aria-orientation="vertical" aria-label="파일 트리 너비 조절" '
    'aria-controls="shell-nav" tabindex="0" data-action="nav-resize"></div>'
)


def _shortcuts_dialog(meeting_guide: bool) -> str:
    extra = ('\n  <p class="shortcuts-extra"><button type="button" class="btn" data-action="meeting-guide-open" hidden>'
             '회의 모드 안내 보기</button></p>') if meeting_guide else ""
    return f"""<dialog id="shortcuts-dialog" aria-labelledby="shortcuts-title">
  <h2 id="shortcuts-title">단축키</h2>
  <dl class="keys">
    <dt><kbd>⌘</kbd> <kbd>K</kbd> · <kbd>Ctrl</kbd> <kbd>K</kbd> · <kbd>/</kbd></dt><dd>문서 검색</dd>
    <dt><kbd>t</kbd></dt><dd>이 문서 목차 열기</dd>
    <dt><kbd>?</kbd></dt><dd>이 안내 열기</dd>
    <dt><kbd>Esc</kbd></dt><dd>열린 창·메뉴 닫기</dd>
  </dl>{extra}
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
        items.append(f'<option value="{esc(r.id)}" data-repo-id="{esc(r.id)}"{sel}>{esc(r.name)}</option>')
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
                  default_ref=None, repo_status=None, meetings_link=False) -> str:
    menu = (f'<button type="button" class="icon-btn shell-menu" data-action="toggle-nav" '
            f'aria-controls="shell-nav" aria-expanded="false" aria-label="메뉴 열기">{_MENU}</button>')
    branch = ""
    if active_repo and branches:
        branch = _select("브랜치", "branch", branch_options(branches, active_ref or default_ref, default_ref))
    status = ""
    if repo_status in STATUS_LABELS:
        status = (f'<span class="shell-status shell-status-{esc(repo_status)}" role="status">'
                  f'{STATUS_LABELS[repo_status]}</span>')
    meetings = (f'<a class="shell-link" href="{esc(view_prefix)}/meetings">회의</a>' if meetings_link else "")
    return f"""<a class="skip-link" href="#main">본문으로 건너뛰기</a>
<header class="shell-header">
  {menu}
  <a class="shell-logo" href="{esc(view_prefix)}/">{_LOGO}<span>synctx</span></a>
  <div class="shell-controls">{_select("저장소", "repo", repo_options(repos, active_repo))}{branch}</div>
  <div class="finder">
    <label class="finder-box">{_SEARCH}<input type="search" id="finder-input" role="combobox" aria-expanded="false" aria-controls="finder-results" aria-autocomplete="list" autocomplete="off" placeholder="문서 검색" aria-label="문서 검색"><kbd aria-hidden="true">⌘K</kbd></label>
    <ul id="finder-results" class="finder-results" role="listbox" aria-label="검색 결과" hidden></ul>
  </div>
  {meetings}
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
         theme_via_query=False, doc_config=None, head_extra="", body_extra="",
         meeting_guide: bool = False) -> str:
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
{_NAV_WIDTH_BOOT}
<style>{THEME_VARS_CSS}</style>
<link rel="icon" type="image/svg+xml" href="{vp}/static/favicon.svg">
{head_extra}
<link rel="stylesheet" href="{vp}/static/viewer.css">
</head>
<body class="{esc(classes)}" data-theme-mode="{mode}">
{header}
{body}
<div class="toast-region" id="toast-region"></div>
{_shortcuts_dialog(meeting_guide)}
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


def render_html_links(*, view_prefix, repo_id, path, ref_param) -> str:
    href = f"{view_prefix}/{quote(repo_id, safe='')}/{quote(path)}?mode=render"
    return (f'<a class="btn" data-action="render-open" href="{esc(href + ref_param)}">렌더링</a>'
            f'<a class="btn" data-action="render-new-tab" href="{esc(href + "&layout=full" + ref_param)}" '
            'target="_blank" rel="noopener noreferrer">새 탭</a>')


def render_html_view(*, frame_url, view_prefix, repo_id, path, ref_param, full=False) -> str:
    href = f"{view_prefix}/{quote(repo_id, safe='')}/{quote(path)}"
    source = href + ("?" + ref_param.lstrip("&") if ref_param else "")
    new_tab = href + "?mode=render&layout=full" + ref_param
    full_attr = ' data-render-full="true"' if full else ""
    return f'''<main id="main" class="html-render" tabindex="-1" data-html-render data-render-layout="fit" data-render-aspect="16/9"{full_attr}>
  <div data-render-bars>
    <p class="render-warning" role="note">저장소의 HTML과 스크립트를 실행합니다. 유효 시간이 지나면 원래 Viewer 화면을 새로 고치세요.</p>
    <div class="render-toolbar" role="toolbar" aria-label="HTML 보기">
      <button type="button" class="btn" data-action="render-fit" aria-pressed="true">화면 맞춤</button>
      <button type="button" class="btn" data-action="render-ratio" aria-pressed="false">비율 고정</button>
      <button type="button" class="btn" data-action="render-fullscreen">전체 화면</button>
      <a class="btn" data-action="render-new-tab" href="{esc(new_tab)}" target="_blank" rel="noopener noreferrer">새 탭</a>
      <a class="btn" href="{esc(source)}">소스 보기</a>
    </div>
  </div>
  <div data-render-stage><iframe data-render-frame title="{esc(path)}" sandbox="allow-scripts" src="{esc(frame_url)}"></iframe></div>
</main>'''


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


def _search_form(*, view_prefix, raw_query, repos, active_repo, ref) -> str:
    options = [f'<option value=""{"" if active_repo else " selected"}>모든 저장소</option>']
    for r in repos:
        sel = " selected" if r.id == active_repo else ""
        options.append(f'<option value="{esc(r.id)}" data-repo-id="{esc(r.id)}"{sel}>{esc(r.name)}</option>')
    hidden = ""
    if ref and active_repo:
        hidden = (f'<input type="hidden" name="ref" value="{esc(ref)}">'
                  f'<input type="hidden" name="ref_repo" value="{esc(active_repo)}">')
    return (f'<form class="search-form" role="search" method="get" action="{esc(view_prefix)}/search">'
            f'<label class="search-input">{_SEARCH}<input type="search" name="q" value="{esc(raw_query)}" '
            f'autocomplete="off" aria-label="검색어"></label>'
            f'<label class="shell-field"><span class="shell-field-label">저장소</span>'
            f'<select name="repo">{"".join(options)}</select></label>'
            f'{hidden}<button type="submit" class="btn btn-primary">검색</button></form>')


def _search_group(group, index, *, view_prefix, ref) -> str:
    heading_id = f"search-repo-{index}"
    notes = []
    if group["status"] in SEARCH_STATUS_COPY and group["status"] != "truncated":
        notes.append(f'<p class="search-note search-note-{esc(group["status"])}">{SEARCH_STATUS_COPY[group["status"]]}</p>')
    if group["truncated"]:
        notes.append(f'<p class="search-note search-note-truncated">{SEARCH_STATUS_COPY["truncated"]}</p>')
    items = []
    for m in group["matches"]:
        href = _doc_href(view_prefix, group["id"], m["path"], ref)
        mark = f'<mark>{esc(m["match"])}</mark>' if m["match"] else ""
        items.append(
            f'<li class="search-hit"><a class="search-hit-path" href="{esc(href)}">{esc(m["path"])}</a>'
            f'<span class="search-hit-line">{esc(m["line"])}행</span>'
            f'<code class="search-hit-text">{esc(m["before"])}{mark}{esc(m["after"])}</code></li>')
    listing = f'<ol class="search-hits">{"".join(items)}</ol>' if items else ""
    return (f'<section class="search-group" aria-labelledby="{heading_id}">'
            f'<h2 id="{heading_id}">{esc(group["name"])}</h2>{"".join(notes)}{listing}</section>')


def render_search_page(*, view_prefix: str, query: str, raw_query: str, repos: Sequence,
                       active_repo: str | None, ref: str | None, results: list[dict] | None) -> str:
    title = f"\u2018{esc(query)}\u2019 검색 결과" if results is not None else "문서 검색"
    parts = ['<main id="main" class="search-page" tabindex="-1">', f"<h1>{title}</h1>",
             _search_form(view_prefix=view_prefix, raw_query=raw_query, repos=repos, active_repo=active_repo, ref=ref)]
    if results is None and raw_query.strip():
        parts.append(f'<p class="search-note search-note-invalid" role="alert">{SEARCH_QUERY_HINT}</p>')
    elif results is None:
        parts.append(f'<p class="search-note">{SEARCH_QUERY_HINT}</p>')
    else:
        total = sum(len(g["matches"]) for g in results)
        found = sum(1 for g in results if g["matches"])
        parts.append(f'<p class="search-summary" role="status">{found}개 저장소에서 {total}건</p>')
        searched_nothing = not results
        if (total == 0 and (searched_nothing or any(g["status"] == "ok" for g in results))
                and not any(g["truncated"] for g in results)):
            parts.append('<p class="search-note search-note-empty">찾은 결과가 없습니다</p>')
        for index, group in enumerate(results):
            if group["matches"] or group["truncated"] or group["status"] != "ok":
                parts.append(_search_group(group, index, view_prefix=view_prefix,
                                           ref=ref if ref and group["id"] == active_repo else None))
    parts.append("</main>")
    return "".join(parts)


def render_jitsi_join(data: dict | None, *, location: str) -> str:
    if data is None:
        return ""
    reason = data.get("reason", "")
    disabled = " disabled" if reason else ""
    status_id = f"jitsi-reason-{location}"
    return (f'<span class="jitsi-join-controls"><button type="button" class="btn jitsi-join" '
            f'data-jitsi-join aria-label="영상 회의 참여" aria-controls="jitsi-panel" '
            f'aria-expanded="false" aria-describedby="{status_id}"{disabled}>참여</button>'
            f'<span id="{status_id}" data-jitsi-reason role="status">{esc(reason)}</span></span>')


def render_meeting_card(data: dict, dashboard_href: str | None = None) -> str:
    rows = [("일시", data["time_label"])]
    if data["presenters"]:
        rows.append(("발표자", ", ".join(data["presenters"])))
    if data["channel"]:
        rows.append(("채널", data["channel"]))
    facts = "".join(f"<div><dt>{label}</dt><dd>{esc(value)}</dd></div>" for label, value in rows)
    status = "취소된 회의" if data["cancelled"] else ""
    agenda = ""
    if data["agenda"]:
        items = "".join(f'<li><a href="#{esc(anchor)}">{esc(name)}</a></li>' for anchor, name in data["agenda"])
        agenda = f'<nav class="agenda" aria-label="안건"><ol>{items}</ol></nav>'
    dashboard = (f'<p class="meeting-info-dashboard"><a href="{esc(dashboard_href)}">회의 대시보드로</a></p>'
                 if dashboard_href else "")
    return (f'<section class="meeting-info" aria-label="회의 정보" data-starts-at="{esc(data["starts_at_iso"])}" '
            f'data-duration="{esc(data["duration_minutes"])}" data-cancelled="{"true" if data["cancelled"] else "false"}">'
            f'<h2 class="meeting-info-title">{esc(data["title"])}</h2>'
            f'<dl class="meeting-info-facts">{facts}</dl>'
            f'<p class="meeting-info-status" role="status" aria-live="polite">{status}</p>'
            f'{agenda}{dashboard}{render_jitsi_join(data.get("jitsi"), location="card")}</section>')


MEETING_STATUS_LABELS = {"upcoming": "예정", "ongoing": "진행 중", "ended": "종료", "cancelled": "취소"}
MEETINGS_TABS = ("all", *STATUSES, "decisions", "actions")
MEETINGS_TAB_LABELS = {"all": "전체", **MEETING_STATUS_LABELS, "decisions": "결정사항", "actions": "액션 아이템"}
MEETINGS_REPO_NOTES = {
    "unavailable": "저장소를 아직 받지 못해 회의를 읽지 못했습니다.",
    "unsupported": "부분 복제 저장소는 회의 목록을 지원하지 않습니다.",
    "timeout": "회의를 찾는 데 시간이 오래 걸려 일부만 읽었을 수 있습니다.",
    "failed": "회의를 읽지 못했습니다. 잠시 후 다시 시도하세요.",
}
MEETINGS_TRUNCATED_NOTE = "회의가 많아 일부만 표시합니다."


def _meetings_href(view_prefix: str, query: dict, **overrides) -> str:
    merged = {**query, **overrides}
    pairs = "&".join(f"{quote(key)}={quote(str(value), safe='')}" for key, value in merged.items() if value)
    return f"{view_prefix}/meetings" + (f"?{pairs}" if pairs else "")


def _meetings_filter_form(view_prefix, tab, query, choices) -> str:
    def select(name, label, options):
        items = [f'<option value="">전체</option>'] + [
            f'<option value="{esc(value)}"{" selected" if value == query.get(name) else ""}>{esc(text)}</option>'
            for value, text in options]
        return (f'<label class="shell-field"><span class="shell-field-label">{label}</span>'
                f'<select name="{name}">{"".join(items)}</select></label>')

    def date_input(name, label):
        return (f'<label class="shell-field"><span class="shell-field-label">{label}</span>'
                f'<input type="date" name="{name}" value="{esc(query.get(name, ""))}"></label>')

    order_selected = " selected" if query.get("order") == "desc" else ""
    hidden = f'<input type="hidden" name="tab" value="{esc(tab)}">' if tab != "all" else ""
    return (f'<form class="meetings-filter" method="get" action="{esc(view_prefix)}/meetings" aria-label="회의 필터">'
            f'<label class="search-input"><input type="search" name="q" value="{esc(query.get("q", ""))}" '
            f'autocomplete="off" aria-label="회의 제목 검색" placeholder="회의 제목"></label>'
            f'{select("repo", "저장소", choices["repos"])}{select("channel", "채널", [(c, c) for c in choices["channels"]])}'
            f'{select("presenter", "발표자", [(p, p) for p in choices["presenters"]])}'
            f'{select("owner", "담당자", [(o, o) for o in choices["owners"]]) if tab == "actions" else ""}'
            f'{date_input("from", "시작일")}{date_input("to", "종료일")}'
            f'<label class="shell-field"><span class="shell-field-label">정렬</span><select name="order">'
            f'<option value="">오래된 순</option><option value="desc"{order_selected}>최신 순</option></select></label>'
            f'{hidden}<button type="submit" class="btn btn-primary">적용</button>'
            f'<a class="btn" href="{esc(view_prefix)}/meetings{"?tab=" + esc(tab) if tab != "all" else ""}">초기화</a></form>')


def _meeting_links(view_prefix, m) -> str:
    doc = _doc_href(view_prefix, m.repo_id, m.path, None)
    return (f'<a href="{esc(doc)}">문서</a> <a href="{esc(doc + "?mode=present")}">발표</a>')


def _memo_list(items) -> str:
    rows = []
    for item in items:
        extra = " ".join(part for part in (f"담당 {item.owner}" if item.owner else "",
                                           f"기한 {item.due}" if item.due else "") if part)
        suffix = f' <span class="meetings-meta">({esc(extra)})</span>' if extra else ""
        rows.append(f'<li>{esc(item.text)}{suffix}</li>')
    return f'<ul>{"".join(rows)}</ul>' if rows else ""


def _meeting_row(view_prefix, m, now) -> str:
    status = status_of(m, now)
    memo = ""
    if m.decisions or m.actions or m.memo_error:
        parts = []
        if m.decisions:
            parts.append(f'<h3>결정사항</h3>{_memo_list(m.decisions)}')
        if m.actions:
            parts.append(f'<h3>액션 아이템</h3>{_memo_list(m.actions)}')
        if m.memo_error:
            parts.append(f'<p class="meetings-note">{esc(m.memo_error)}</p>')
        memo = (f'<details class="meetings-memo"><summary>결정 {len(m.decisions)}건 · 액션 {len(m.actions)}건</summary>'
                f'{"".join(parts)}</details>')
    return (f'<tr><td data-label="일시">{esc(time_range_label(m.starts_at, m.duration_minutes))}</td>'
            f'<td data-label="제목"><a href="{esc(_doc_href(view_prefix, m.repo_id, m.path, None))}">{esc(m.title)}</a>{memo}</td>'
            f'<td data-label="상태"><span class="meetings-status meetings-status-{status}">{MEETING_STATUS_LABELS[status]}</span></td>'
            f'<td data-label="저장소">{esc(m.repo_name)}</td><td data-label="채널">{esc(m.channel)}</td>'
            f'<td data-label="발표자">{esc(", ".join(m.presenters))}</td>'
            f'<td data-label="바로가기">{_meeting_links(view_prefix, m)}</td></tr>')


def _meetings_table(view_prefix, meetings, now) -> str:
    if not meetings:
        return '<p class="meetings-empty" role="status">조건에 맞는 회의가 없습니다.</p>'
    head = "".join(f'<th scope="col">{label}</th>' for label in
                   ("일시", "제목", "상태", "저장소", "채널", "발표자", "바로가기"))
    rows = "".join(_meeting_row(view_prefix, m, now) for m in meetings)
    return f'<table class="meetings-table"><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>'


def _decisions_view(view_prefix, meetings) -> str:
    groups = []
    for m in meetings:
        if m.decisions:
            link = f'<a href="{esc(_doc_href(view_prefix, m.repo_id, m.path, None))}">{esc(m.title)}</a>'
            groups.append(f'<section class="meetings-group"><h2>{link}</h2>'
                          f'<p class="meetings-meta">{esc(time_range_label(m.starts_at, m.duration_minutes))} · {esc(m.repo_name)}</p>'
                          f'{_memo_list(m.decisions)}</section>')
    return "".join(groups) or '<p class="meetings-empty" role="status">기록된 결정사항이 없습니다.</p>'


def _actions_view(view_prefix, rows, now) -> str:
    if not rows:
        return '<p class="meetings-empty" role="status">기록된 액션 아이템이 없습니다.</p>'
    today = now.astimezone(KST).date().isoformat()
    body = []
    for row in rows:
        m, item = row.meeting, row.item
        overdue = (' <span class="meetings-overdue">기한 지남</span>'
                   if item.due and item.due < today and not m.cancelled else "")
        link = f'<a href="{esc(_doc_href(view_prefix, m.repo_id, m.path, None))}">{esc(m.title)}</a>'
        body.append(f'<tr><td data-label="할 일">{esc(item.text)}</td><td data-label="담당자">{esc(item.owner)}</td>'
                    f'<td data-label="기한">{esc(item.due)}{overdue}</td><td data-label="회의">{link}</td></tr>')
    head = "".join(f'<th scope="col">{label}</th>' for label in ("할 일", "담당자", "기한", "회의"))
    return f'<table class="meetings-table"><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table>'


def render_meetings_page(*, view_prefix, tab, query, choices, meetings, invalid, repo_notes, truncated, now,
                         has_repos) -> str:
    """meetings는 상태 탭을 뺀 나머지 필터를 적용한 전체 목록이다. 탭 선택은 여기서 한다."""
    if tab not in MEETINGS_TABS:
        tab = "all"
    rows = action_items(meetings, query.get("owner") or None)
    counts = {"all": len(meetings), "decisions": sum(len(m.decisions) for m in meetings), "actions": len(rows)}
    for key in STATUSES:
        counts[key] = sum(1 for m in meetings if status_of(m, now) == key)
    current = ' aria-current="page"'
    tab_links = "".join(
        f'<li><a href="{esc(_meetings_href(view_prefix, query, tab=None if key == "all" else key))}"'
        f'{current if key == tab else ""}>{MEETINGS_TAB_LABELS[key]} '
        f'<span class="meetings-count">{counts[key]}</span></a></li>' for key in MEETINGS_TABS)
    parts = ['<main id="main" class="meetings-page" tabindex="-1">', "<h1>회의</h1>"]
    if not has_repos:
        parts.append('<div class="empty-state"><p>회의 연동(collab.enabled)이 켜진 저장소가 없습니다.</p></div></main>')
        return "".join(parts)
    parts.append(f'<nav class="meetings-tabs" aria-label="회의 보기"><ul>{tab_links}</ul></nav>')
    parts.append(_meetings_filter_form(view_prefix, tab, query, choices))
    for repo_name, status in repo_notes:
        parts.append(f'<p class="search-note" role="status">{esc(repo_name)}: {MEETINGS_REPO_NOTES[status]}</p>')
    if truncated:
        parts.append(f'<p class="search-note" role="status">{MEETINGS_TRUNCATED_NOTE}</p>')
    if tab == "decisions":
        parts.append(_decisions_view(view_prefix, meetings))
    elif tab == "actions":
        parts.append(_actions_view(view_prefix, rows, now))
    else:
        shown = meetings if tab == "all" else [m for m in meetings if status_of(m, now) == tab]
        parts.append(_meetings_table(view_prefix, shown, now))
    if invalid:
        items = "".join(f'<li><a href="{esc(_doc_href(view_prefix, i.repo_id, i.path, None))}">{esc(i.path)}</a> '
                        f'<span class="meetings-meta">{esc(i.repo_name)} — {esc(i.reason)}</span></li>' for i in invalid)
        parts.append(f'<section class="meetings-invalid" aria-labelledby="meetings-invalid-title">'
                     f'<h2 id="meetings-invalid-title">확인 필요</h2><ul>{items}</ul></section>')
    parts.append("</main>")
    return "".join(parts)


MEETING_GUIDE_ITEMS = (
    "메모는 이 브라우저에만 저장되고 다른 사람과 공유되지 않습니다.",
    "문단 옆 「+ 메모」를 누르거나 본문을 드래그해 선택하면 메모를 남길 수 있습니다.",
    "오른쪽 아래 「메모」 버튼으로 패널을 열면 결정·액션 메모가 「회의 정리」 탭에 모입니다.",
    "메모를 함께 보려면 패널의 「.md로 내보내기」로 받은 파일을 전하고, 받은 사람은 「가져오기」로 합칩니다.",
)


def render_meeting_exit(exit_href: str) -> str:
    items = "".join(f"<li>{esc(text)}</li>" for text in MEETING_GUIDE_ITEMS)
    return ('<div class="meeting-exit-row">'
            f'<a class="meeting-exit" href="{esc(exit_href)}">회의 모드 나가기</a>'
            '<button type="button" class="meeting-guide-open" data-action="meeting-guide-open" '
            'aria-controls="meeting-guide" hidden>사용법</button>'
            '</div>'
            '<section id="meeting-guide" class="meeting-guide" aria-labelledby="meeting-guide-title" hidden>'
            '<h2 id="meeting-guide-title" class="meeting-guide-title" tabindex="-1">회의 모드 안내</h2>'
            f'<ul>{items}</ul>'
            '<div class="meeting-guide-actions">'
            '<button type="button" class="btn" data-action="meeting-guide-never">다시 보지 않기</button>'
            '<button type="button" class="btn" data-action="meeting-guide-close">닫기</button>'
            '</div></section>')


def render_present_bar(*, title: str, exit_href: str, memo: bool, jitsi: dict | None = None) -> str:
    memo_button = ('<button type="button" class="btn" data-action="present-memo" aria-keyshortcuts="N" '
                   'aria-controls="meeting-panel" aria-expanded="false">'
                   '메모 보기(N)</button>') if memo else ""
    return (f'<header class="present-top"><span class="present-title">{esc(title)}</span>'
            f'<span class="present-time" data-present-time></span>{render_jitsi_join(jitsi, location="present")}</header>'
            '<nav class="present-nav" aria-label="발표 이동">'
            '<div class="present-progress" role="progressbar" aria-label="발표 진행" aria-valuemin="1">'
            '<span class="present-progress-bar"></span></div>'
            '<button type="button" class="btn" data-action="present-prev" aria-keyshortcuts="ArrowLeft PageUp">이전(←)</button>'
            '<span class="present-count"></span>'
            '<button type="button" class="btn" data-action="present-next" aria-keyshortcuts="ArrowRight PageDown Space">다음(→)</button>'
            f'{memo_button}'
            f'<a class="btn" href="{esc(exit_href)}" data-present-exit aria-keyshortcuts="Escape">발표 끝내기(Esc)</a>'
            '</nav>'
            '<p class="present-empty" hidden>발표할 내용이 없습니다</p>'
            '<div class="visually-hidden" aria-live="polite" data-present-live></div>')


EVIDENCE_IDS = {"list": "evidence-list", "title": "evidence-title", "data": "evidence-data"}


def render_evidence_list(entries, *, ids=None, href_for=None) -> str:
    """근거 모음. 문서별 묶음(처음 나온 순서) 안에 항목을 둔다. 발췌 html은 이미 정화된 것을 그대로 넣는다."""
    if not entries:
        return ""
    ids = ids or EVIDENCE_IDS
    groups: dict[tuple[str, str], tuple[str, list[str]]] = {}
    for e in entries:
        cites = " · ".join(f'<a href="#{esc(cid)}">{esc(label)}</a>' for cid, label in e.cites)
        meta = f"인용한 곳: {cites}" if cites else ""
        if href_for:
            source = f'<a href="{esc(href_for(e))}">원문 열기</a>'
            meta = f"{meta} · {source}" if meta else source
        meta = f'<p class="evidence-meta">{meta}</p>' if meta else ""
        groups.setdefault((e.repo_id, e.path), (e.doc, []))[1].append(
            f'<article class="evidence-entry" id="{esc(e.id)}" tabindex="-1">'
            f'<p class="evidence-loc">{esc(e.loc)}</p><div class="evidence-body">{e.html}</div>{meta}</article>')
    docs = "".join(f'<section class="evidence-doc"><h3>{esc(doc)}</h3>{"".join(items)}</section>'
                   for doc, items in groups.values())
    return (f'<section class="evidence-list" id="{esc(ids["list"])}" aria-labelledby="{esc(ids["title"])}">'
            f'<h2 id="{esc(ids["title"])}">근거 모음</h2><p class="evidence-count">근거 {len(entries)}건</p>'
            f'{docs}</section>')


def render_evidence_data(entries, *, ids=None) -> str:
    ids = ids or EVIDENCE_IDS
    data = {"schema": 1, "entries": [{"id": e.id, "doc": e.doc, "loc": e.loc, "html": e.html} for e in entries]}
    return (f'<script type="application/json" id="{esc(ids["data"])}" data-synctx-evidence>'
            f"{encode_for_script(data)}</script>")

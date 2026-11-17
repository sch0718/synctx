"""메뉴별 도움말의 단계와 Viewer 화면.

문구는 Viewer 화면의 실제 문구를 그대로 쓴다(services/viewer/src의 viewer_ui.py·app.py·static/*.js).
좌표는 help.py의 VIEW(브라우저 창 안쪽, x 404–932, y 60–492) 기준이다.
"""
from __future__ import annotations

from .help import VIEW, Ctx, Page, Step
from .kit import button, chip, fit, lines, rect, tap, text, text_width

VX, VY = VIEW["x"], VIEW["y"]
VR, VB = VX + VIEW["w"], VY + VIEW["h"]
BAR = 36  # Viewer 상단 바 높이
TOP = VY + BAR
NAV_W = 128  # 3단 화면의 파일 트리 폭
BODY_X, BODY_R = VX + NAV_W + 14, VR - 122
TOC_X = BODY_R + 14

REPO = "team-docs"
MEETING_DOC = "회의/2026-10-27-정기회의.md"
DOC_URL = f"/view/{REPO}/설계/아키텍처.md"
MEETING_URL = f"/view/{REPO}/{MEETING_DOC}"
TREE = [(0, "회의", "dir"), (1, "2026-10-27-정기회의.md", "file"), (0, "설계", "dir"),
        (1, "아키텍처.md", "file"), (1, "동기화.md", "file"), (0, "규정", "dir"), (1, "보안규정.md", "file"),
        (0, "발표", "dir"), (1, "분기보고.md", "file"), (0, "README.md", "file")]


# ── 공통 조각 ───────────────────────────────────────────────

def rise(c: Ctx, offset: float, body: str) -> str:
    return f'<g class="rise" style="{c.d(offset)}">{body}</g>'


def pop(c: Ctx, offset: float, body: str) -> str:
    return f'<g class="pop" style="{c.d(offset)}">{body}</g>'


def tap_at(c: Ctx, x, y, offset: float = 2.2) -> str:
    return tap(x, y, c.t, c.d(offset))


def select(c: Ctx, x, y, w, value, *, label=None) -> str:
    t = c.t
    head = text(x, y - 3, label, size=8.5, weight=700, fill=t["t3"]) if label else ""
    return (head + rect(x, y, w, 20, rx=5, fill=t["surface"], stroke=t["line"])
            + text(x + 7, y + 14, fit(value, 10.5, w - 22), size=10.5, weight=600, fill=t["t1"])
            + text(x + w - 7, y + 13, "⌄", size=10, fill=t["t2"], anchor="end"))


def topbar(c: Ctx, *, query: str | None = None, focus: bool = False, repo: str = REPO) -> str:
    """Viewer 상단 바: 로고, 저장소·브랜치, 문서 검색(⌘K), 테마, ?."""
    t = c.t
    theme = "GitHub 어둡게" if c.dark else "GitHub 밝게"
    y = VY
    logo = (f'<circle cx="{VX + 16}" cy="{y + 18}" r="3.2" fill="none" stroke="{t["t1"]}" stroke-width="1.6"/>'
            f'<path d="M{VX + 16} {y + 9} v4 m0 10 v4 M{VX + 7} {y + 18} h4 m10 0 h4" stroke="{t["t1"]}" stroke-width="1.6"/>'
            + text(VX + 30, y + 22, "synctx", size=12, weight=800, fill=t["t1"]))
    box_x, box_w = VX + 222, 168
    stroke = t["accent"] if focus else t["line"]
    search = (rect(box_x, y + 8, box_w, 21, rx=6, fill=t["surface2"], stroke=stroke)
              + text(box_x + 10, y + 22, "⌕", size=11, fill=t["t2"])
              + (text(box_x + 24, y + 22, query, size=10.5, weight=600, fill=t["t1"]) if query
                 else text(box_x + 24, y + 22, "문서 검색", size=10.5, fill=t["t3"]))
              + rect(box_x + box_w - 30, y + 12, 24, 13, rx=3, fill=t["surface"], stroke=t["line"])
              + text(box_x + box_w - 18, y + 22, "⌘K", size=8.5, weight=700, fill=t["t2"], anchor="middle"))
    caret = (f'<rect class="fade" style="{c.d(0.4)}" x="{box_x + 25 + text_width(query or "", 10.5)}" y="{y + 12}" '
             f'width="1.4" height="13" fill="{t["accent"]}"/>') if focus else ""
    return (rect(VX, y, VIEW["w"], BAR, rx=0, fill=t["surface"]) + f'<path d="M{VX} {y + BAR} H{VR}" stroke="{t["line"]}"/>'
            + logo + select(c, VX + 82, y + 8, 70, repo) + select(c, VX + 158, y + 8, 56, "main")
            + search + caret + select(c, VR - 104, y + 8, 74, theme)
            + f'<circle cx="{VR - 15}" cy="{y + 18}" r="9" fill="none" stroke="{t["line"]}"/>'
            + text(VR - 15, y + 22, "?", size=11, weight=800, fill=t["t2"], anchor="middle"))


def tree(c: Ctx, *, selected: str | None = None, open_dirs=("회의", "설계", "규정"), filter_box=True) -> str:
    """왼쪽 파일 트리(‘파일 이름으로 좁히기’, ‘파일’·‘이 문서’ 탭)."""
    t = c.t
    out = [rect(VX, TOP, NAV_W, VIEW["h"] - BAR, rx=0, fill=t["surface2"]),
           f'<path d="M{VX + NAV_W} {TOP} V{VB}" stroke="{t["line"]}"/>']
    y = TOP + 12
    if filter_box:
        out.append(rect(VX + 8, y, NAV_W - 16, 20, rx=5, fill=t["surface"], stroke=t["line"])
                   + text(VX + 15, y + 14, fit("파일 이름으로 좁히기", 9.5, NAV_W - 30), size=9.5, fill=t["t3"]))
        y += 30
    out.append(text(VX + 12, y + 8, "파일", size=10, weight=800, fill=t["t1"])
               + f'<path d="M{VX + 10} {y + 13} h26" stroke="{t["accent"]}" stroke-width="2"/>'
               + text(VX + 48, y + 8, "이 문서", size=10, weight=600, fill=t["t3"]))
    y += 24
    current_dir = None
    for depth, name, kind in TREE:
        if depth == 0:
            current_dir = name
        elif current_dir not in open_dirs:
            continue
        x = VX + 12 + depth * 12
        if name == selected:
            out.append(rect(VX + 6, y - 11, NAV_W - 12, 17, rx=4, fill=t["accent_soft"]))
        icon = ("▾" if name in open_dirs else "▸") if kind == "dir" else "·"
        out.append(text(x, y + 1, icon, size=9, fill=t["t2"])
                   + text(x + 10, y + 1, fit(name, 10, NAV_W - (x - VX) - 18), size=10,
                          weight=800 if name == selected else 500, fill=t["accent"] if name == selected else t["t1"]))
        y += 19
    return "".join(out)


def toc(c: Ctx, items, active: int = 0) -> str:
    t = c.t
    out = [text(TOC_X, TOP + 22, "이 문서", size=10, weight=800, fill=t["t2"])]
    for i, name in enumerate(items):
        y = TOP + 44 + i * 20
        if i == active:
            out.append(f'<path d="M{TOC_X - 6} {y - 10} v14" stroke="{t["accent"]}" stroke-width="2"/>')
        out.append(text(TOC_X, y + 1, name, size=10, weight=700 if i == active else 500,
                        fill=t["accent"] if i == active else t["t2"]))
    out.append(text(TOC_X, TOP + 56 + len(items) * 20, "맨 위로", size=9.5, fill=t["t3"]))
    return "".join(out)


def heading(c: Ctx, x, y, value, size=17) -> str:
    return text(x, y, value, size=size, weight=800, fill=c.t["t1"])


def toolbar(c: Ctx, x, y, labels, *, primary=None) -> tuple[str, dict[str, tuple[float, float]]]:
    """문서 위 단추 줄. 단추 가운데 좌표를 함께 돌려준다."""
    out, centers = [], {}
    for name in labels:
        w, svg = button(x, y, name, c.t, variant="filled" if name == primary else "outlined", h=22, size=10)
        out.append(svg)
        centers[name] = (x + w / 2, y + 11)
        x += w + 6
    return "".join(out), centers


def menu(c: Ctx, x, y, w, items, *, active=None, offset=0.3) -> str:
    t = c.t
    h = 10 + len(items) * 20
    rows = []
    for i, name in enumerate(items):
        ry = y + 6 + i * 20
        if name == active:
            rows.append(rect(x + 4, ry, w - 8, 18, rx=4, fill=t["accent_soft"]))
        rows.append(text(x + 12, ry + 13, name, size=10.5, weight=700 if name == active else 500,
                         fill=t["accent"] if name == active else t["t1"]))
    return rise(c, offset, rect(x, y, w, h, rx=8, fill=t["surface"], stroke=t["line"]) + "".join(rows))


def toast(c: Ctx, title: str, offset: float, *, action: str | None = None, kind: str = "progress") -> str:
    t = c.t
    # 같은 자리에 이어 뜨는 알림이 앞 알림을 덮도록 폭을 맞춘다.
    w = max(190, text_width(title, 11) + (text_width(action, 11) + 26 if action else 0) + 40)
    x, y = VR - w - 16, VB - 46
    mark = {"progress": "◌", "success": "✓", "error": "!"}[kind]
    body = (rect(x, y, w, 30, rx=8, fill=t["t1"]) + text(x + 14, y + 19, mark, size=11, weight=800, fill=t["bg"])
            + text(x + 30, y + 19, title, size=11, weight=700, fill=t["bg"]))
    if action:
        body += text(x + w - 14, y + 19, action, size=11, weight=800, fill=t["accent_soft"], anchor="end")
    return rise(c, offset, body)


def doc_view(c: Ctx, *, title="아키텍처", toc_items=("개요", "구성", "배포", "운영"), toc_active=0,
             selected="아키텍처.md", body: str = "", show_toc=True) -> str:
    """3단 문서 화면(파일 트리 · 본문 · 목차)."""
    return (topbar(c) + tree(c, selected=selected) + heading(c, BODY_X, TOP + 30, title)
            + body + (toc(c, toc_items, toc_active) if show_toc else ""))


def mermaid(c: Ctx, x, y, offset=0.6) -> str:
    """본문 속 Mermaid 그림(외부망 Git → 내부 Git → Viewer)."""
    t = c.t
    boxes = [("외부망 Git", 0), ("내부 Git", 92), ("Viewer", 184)]
    out = []
    for name, dx in boxes:
        out.append(rect(x + dx, y, 76, 28, rx=6, fill=t["accent_soft"], stroke=t["accent"])
                   + text(x + dx + 38, y + 18, name, size=10, weight=700, fill=t["t1"], anchor="middle"))
    for dx in (76, 168):
        out.append(f'<path d="M{x + dx + 2} {y + 14} h12" stroke="{t["t2"]}" stroke-width="1.5"/>'
                   f'<path d="M{x + dx + 14} {y + 10} l4 4 -4 4" fill="none" stroke="{t["t2"]}" stroke-width="1.5"/>')
    return rise(c, offset, "".join(out))


def code_block(c: Ctx, x, y, w, offset=0.9) -> str:
    t = c.t
    colors = [t["accent"], t["action"], t["decision"], t["t2"]]
    bars = "".join(rect(x + 12 + (i % 2) * 14, y + 10 + i * 12, wd, 5, rx=2.5, fill=colors[i % len(colors)])
                   for i, wd in enumerate((120, 90, 150, 70)))
    return rise(c, offset, rect(x, y, w, 60, rx=6, fill=t["surface2"], stroke=t["line"]) + bars)


def meeting_card(c: Ctx, x, y, w, offset=0.2) -> str:
    """회의 정보 카드(일시·발표자·채널·진행 상태)."""
    t = c.t
    facts = [("일시", "10/27(월) 10:00–11:00"), ("발표자", "김민지"), ("채널", "platform-weekly")]
    rows = "".join(text(x + 12, y + 20 + i * 16, name, size=9.5, weight=700, fill=t["t3"])
                   + text(x + 52, y + 20 + i * 16, value, size=10, fill=t["t1"]) for i, (name, value) in enumerate(facts))
    _, status = chip(x + 12, y + 66, "진행 중 · 12분 지남 · 48분 남음", t, size=9.5, h=18, fill=t["accent_soft"], color=t["accent"])
    return rise(c, offset, rect(x, y, w, 92, rx=8, fill=t["surface"], stroke=t["line"]) + rows + status)


def mark(c: Ctx, x, y, w, *, kind="note", offset=0.0) -> str:
    """형광펜(회의 모드 메모가 걸린 글)."""
    t = c.t
    fill = "rgba(255,170,80,0.3)" if kind == "action" else t["hl"]
    line = {"note": t["hl_line"], "decision": t["decision"], "action": t["action"]}[kind]
    return (f'<g class="fade" style="{c.d(offset)}">{rect(x, y - 9, w, 13, rx=2, fill=fill)}'
            f'<path d="M{x} {y + 4} h{w}" stroke="{line}" stroke-width="2"/></g>')


def panel(c: Ctx, tab: str, body: str, *, x=VR - 196) -> str:
    """회의 모드 오른쪽 패널(‘메모’·‘회의 정리’ 탭)."""
    t = c.t
    tabs = ""
    tx = x + 12
    for name in ("메모", "회의 정리"):
        w = text_width(name, 10.5) + 16
        if name == tab:
            tabs += rect(tx, TOP + 10, w, 20, rx=5, fill=t["surface2"], stroke=t["line"])
        tabs += text(tx + w / 2, TOP + 24, name, size=10.5, weight=800 if name == tab else 500,
                     fill=t["t1"] if name == tab else t["t2"], anchor="middle")
        tx += w + 6
    return rise(c, 0.1, rect(x, TOP, VR - x, VIEW["h"] - BAR, rx=0, fill=t["bg"])
                + f'<path d="M{x} {TOP} V{VB}" stroke="{t["line"]}"/>' + tabs
                + text(VR - 14, TOP + 25, "×", size=14, fill=t["t2"], anchor="end") + body)


def badge(c: Ctx, x, y, kind: str) -> tuple[float, str]:
    t = c.t
    name = {"note": "일반", "decision": "결정", "action": "액션"}[kind]
    fill = {"note": t["surface2"], "decision": t["decision"], "action": t["action"]}[kind]
    color = t["t1"] if kind == "note" else "#ffffff"
    return chip(x, y, name, t, size=9, h=16, fill=fill, color=color)


# ── 1. 저장소·문서 찾아가기 ─────────────────────────────────

def browse_list(c: Ctx) -> str:
    t = c.t
    cards = [("team-docs", "설계·회의·규정 문서"), ("meeting-notes", "주간 회의록"), ("harness", "AI 하네스 규칙")]
    out = [topbar(c, repo="저장소 선택"), heading(c, VX + 20, TOP + 34, "저장소", 18)]
    for i, (name, desc) in enumerate(cards):
        x = VX + 20 + (i % 2) * 248
        y = TOP + 50 + (i // 2) * 76
        out.append(rise(c, 0.2 + i * 0.15,
                        rect(x, y, 236, 64, rx=8, fill=t["surface"], stroke=t["accent"] if i == 0 else t["line"])
                        + text(x + 14, y + 22, name, size=12.5, weight=800, fill=t["t1"])
                        + text(x + 14, y + 39, "기본 브랜치 main", size=10, fill=t["t2"])
                        + text(x + 14, y + 54, desc, size=9.5, fill=t["t3"])))
    out.append(heading(c, VX + 20, TOP + 232, "최근 본 문서", 13))
    for i, name in enumerate((MEETING_DOC, "설계/아키텍처.md")):
        y = TOP + 252 + i * 24
        out.append(rise(c, 0.8 + i * 0.1, text(VX + 20, y, "·", size=12, fill=t["t2"])
                        + text(VX + 32, y, name, size=10.5, weight=600, fill=t["accent"])
                        + text(VX + 40 + text_width(name, 10.5), y, f"· {REPO}", size=10, fill=t["t3"])))
    out.append(tap_at(c, VX + 138, TOP + 82))
    return "".join(out)


def browse_home(c: Ctx) -> str:
    t = c.t
    x = BODY_X
    _, readme = button(x + 12, TOP + 144, "README 열기", t, variant="filled", h=22, size=10)
    info = rise(c, 0.2, rect(x, TOP + 58, VR - x - 16, 116, rx=8, fill=t["surface"], stroke=t["line"])
                + text(x + 12, TOP + 78, "저장소 정보", size=11, weight=800, fill=t["t1"])
                + text(x + 12, TOP + 98, "기본 브랜치", size=9.5, weight=700, fill=t["t3"])
                + text(x + 72, TOP + 98, "main", size=10, fill=t["t1"])
                + text(x + 12, TOP + 116, "최근", size=9.5, weight=700, fill=t["t3"])
                + text(x + 72, TOP + 116, "마지막 커밋 3시간 전", size=10, fill=t["t1"]) + readme)
    hint = rise(c, 0.5, text(x, TOP + 200, "왼쪽 파일 트리에서 문서를 고르거나", size=10.5, fill=t["t2"])
                + text(x, TOP + 216, "위의 문서 검색을 쓰세요.", size=10.5, fill=t["t2"]))
    return (topbar(c) + tree(c) + text(x, TOP + 18, f"저장소 / {REPO}", size=9.5, fill=t["t3"])
            + heading(c, x, TOP + 42, REPO) + info + hint + tap_at(c, VX + 66, TOP + 119))


def browse_doc(c: Ctx) -> str:
    t = c.t
    x = BODY_X
    body = (lines(x, TOP + 46, (250, 230, 120), t, style=c.d(0.3)) + mermaid(c, x, TOP + 96)
            + heading(c, x, TOP + 154, "구성", 13) + lines(x, TOP + 166, (240, 200), t, style=c.d(0.8))
            + code_block(c, x, TOP + 200, BODY_R - x))
    return doc_view(c, body=body)


def browse_toc(c: Ctx) -> str:
    t = c.t
    x = BODY_X
    body = (heading(c, x, TOP + 60, "배포", 13) + lines(x, TOP + 72, (250, 210, 236, 140), t, style=c.d(0.3))
            + heading(c, x, TOP + 150, "운영", 13) + lines(x, TOP + 162, (230, 180), t, style=c.d(0.5)))
    return doc_view(c, toc_active=2, body=body) + tap_at(c, TOC_X + 12, TOP + 85)


# ── 2. 문서 검색 ───────────────────────────────────────────

SEARCH_X, SEARCH_W = VX + 222, 300


def dim_doc(c: Ctx) -> str:
    return tree(c, selected="아키텍처.md") + heading(c, BODY_X, TOP + 30, "아키텍처") + lines(
        BODY_X, TOP + 46, (250, 230, 200, 120), c.t)


def results(c: Ctx, active: int | None = None) -> str:
    t = c.t
    rows = [("파일", None), ("회의/회의록-양식.md", "file"), (MEETING_DOC, "file"),
            ("본문", None), ("규정/보안규정.md · 12행", "hit"), ("‘회의록’ 전체 결과 보기", "all")]
    h = 12 + len(rows) * 22
    out = [rect(SEARCH_X, TOP + 2, SEARCH_W, h, rx=8, fill=t["surface"], stroke=t["line"])]
    index = 0
    for i, (name, kind) in enumerate(rows):
        y = TOP + 8 + i * 22
        if kind is None:
            out.append(text(SEARCH_X + 12, y + 14, name, size=9, weight=800, fill=t["t3"]))
            continue
        if active == index:
            out.append(rect(SEARCH_X + 4, y, SEARCH_W - 8, 20, rx=4, fill=t["accent_soft"]))
        fill = t["accent"] if kind == "all" else t["t1"]
        out.append(text(SEARCH_X + 12, y + 14, name, size=10.5, weight=700 if kind == "all" else 500, fill=fill))
        if kind == "hit":
            out.append(text(SEARCH_X + 160, y + 14, "‘회의록은 5년 보관한다’", size=9.5, fill=t["t2"]))
        index += 1
    return rise(c, 0.3, "".join(out))


def search_open(c: Ctx) -> str:
    t = c.t
    keys = (rise(c, 0.5, text(BODY_X, TOP + 120, "어디서든 이 키로 엽니다", size=10.5, weight=700, fill=t["t2"]))
            + "".join(pop(c, 0.6 + i * 0.15, chip(BODY_X + i * 64, TOP + 132, k, t, size=11, h=24)[1])
                      for i, k in enumerate(("⌘K", "Ctrl K", "/"))))
    return dim_doc(c) + topbar(c, focus=True) + keys + tap_at(c, SEARCH_X + 60, VY + 18)


def search_type(c: Ctx) -> str:
    return dim_doc(c) + topbar(c, query="회의록", focus=True) + results(c)


def search_pick(c: Ctx) -> str:
    t = c.t
    keys = rise(c, 0.6, text(SEARCH_X + 4, TOP + 162, "↑↓ 이동 · Enter 열기 · Esc 닫기", size=10, weight=700, fill=t["t2"]))
    return dim_doc(c) + topbar(c, query="회의록", focus=True) + results(c, active=1) + keys + tap_at(
        c, SEARCH_X + 120, TOP + 62)


def search_page(c: Ctx) -> str:
    t = c.t
    x = VX + 20
    _, go = button(x + 316, TOP + 18, "검색", t, variant="filled", h=22, size=10)
    form = (rect(x, TOP + 18, 210, 22, rx=6, fill=t["surface"], stroke=t["line"])
            + text(x + 10, TOP + 33, "회의록", size=10.5, weight=600, fill=t["t1"])
            + select(c, x + 218, TOP + 19, 90, "모든 저장소") + go)
    out = [topbar(c, query="회의록"), form, heading(c, x, TOP + 70, "‘회의록’ 검색 결과", 16),
           text(x, TOP + 88, "2개 저장소에서 5건", size=10.5, fill=t["t2"])]
    hits = [(REPO, "규정/보안규정.md", "12행", "회의록은 5년 보관한다"),
            (REPO, MEETING_DOC, "3행", "회의록 검토 후 시작"),
            ("meeting-notes", "2026/10월.md", "41행", "회의록 공유 방법")]
    for i, (repo, path, line, snippet) in enumerate(hits):
        y = TOP + 108 + i * 52
        out.append(rise(c, 0.3 + i * 0.15, rect(x, y, VIEW["w"] - 40, 44, rx=8, fill=t["surface"], stroke=t["line"])
                        + text(x + 12, y + 17, path, size=10.5, weight=700, fill=t["accent"])
                        + text(x + 18 + text_width(path, 10.5), y + 17, f"· {repo} · {line}", size=9.5, fill=t["t3"])
                        + mark(c, x + 12, y + 34, text_width("회의록", 10.5), offset=0.6 + i * 0.15)
                        + text(x + 12, y + 34, snippet, size=10.5, fill=t["t1"])))
    return "".join(out)


# ── 3. 문서 보기·내려받기 ──────────────────────────────────

def plain_doc(c: Ctx, *, buttons=("내려받기",)) -> tuple[str, dict]:
    t = c.t
    bar, centers = toolbar(c, BODY_X, TOP + 40, buttons)
    body = (bar + lines(BODY_X, TOP + 76, (250, 230, 120), t) + mermaid(c, BODY_X, TOP + 120, offset=0)
            + code_block(c, BODY_X, TOP + 168, BODY_R - BODY_X, offset=0))
    return doc_view(c, body=body), centers


def document_theme(c: Ctx) -> str:
    doc, _ = plain_doc(c)
    themes = ("GitHub 밝게", "GitHub 어둡게", "Cayman", "Minimal", "Hacker", "Modernist", "Corporate")
    return (doc + menu(c, VR - 112, VY + 30, 100, themes, active="GitHub 어둡게" if c.dark else "GitHub 밝게")
            + tap_at(c, VR - 66, VY + 18, 0.2))


def document_download(c: Ctx) -> str:
    doc, centers = plain_doc(c)
    x, y = centers["내려받기"]
    return doc + menu(c, x - 30, y + 14, 90, ("원본", "HTML", "PDF"), active="HTML", offset=0.5) + tap_at(c, x, y, 0.2)


def document_branch(c: Ctx) -> str:
    doc, _ = plain_doc(c)
    return doc + menu(c, VX + 158, VY + 30, 96, ("main (기본)", "release", "draft/2026-11"), active="release") + tap_at(
        c, VX + 186, VY + 18, 0.2)


def document_keys(c: Ctx) -> str:
    t = c.t
    doc, _ = plain_doc(c)
    x, y, w = VX + 124, TOP + 54, 280
    rows = [("⌘K · Ctrl K · /", "문서 검색"), ("t", "이 문서 목차 열기"), ("?", "이 안내 열기"), ("Esc", "열린 창·메뉴 닫기")]
    body = "".join(text(x + 20, y + 58 + i * 24, key, size=10.5, weight=800, fill=t["t1"], mono=True)
                   + text(x + 130, y + 58 + i * 24, name, size=10.5, fill=t["t2"]) for i, (key, name) in enumerate(rows))
    _, close = button(x + w - 64, y + 160, "닫기", t, h=22, size=10)
    dialog = rise(c, 0.4, rect(VX, TOP, VIEW["w"], VIEW["h"] - BAR, rx=0, fill=t["t1"], extra=' fill-opacity=".18"')
                  + rect(x, y, w, 196, rx=10, fill=t["surface"], stroke=t["line"])
                  + text(x + 20, y + 30, "단축키", size=14, weight=800, fill=t["t1"]) + body + close)
    return doc + dialog + tap_at(c, VR - 15, VY + 18, 0.1)


# ── 4. 회의 모드 ───────────────────────────────────────────

def meeting_doc(c: Ctx, *, mode="view", tap_label=None) -> str:
    t = c.t
    x = BODY_X
    if mode == "view":
        bar, centers = toolbar(c, x, TOP + 40, ("회의 모드로 열기", "발표하기", "내려받기"), primary="회의 모드로 열기")
    else:
        bar = (text(x, TOP + 54, "회의 모드 나가기", size=10.5, weight=700, fill=t["accent"])
               + text(x + 96, TOP + 54, "사용법", size=10.5, fill=t["t2"]))
        centers = {}
    body = (bar + meeting_card(c, x, TOP + 68, BODY_R - x) + heading(c, x, TOP + 186, "1. 지난 회의 확인", 12)
            + lines(x, TOP + 198, (240, 180), t) + heading(c, x, TOP + 244, "2. 결정사항", 12)
            + lines(x, TOP + 256, (230, 200, 120), t))
    out = (topbar(c) + tree(c, selected="2026-10-27-정기회의.md")
           + heading(c, x, TOP + 30, "10월 정기 회의") + body)
    if tap_label:
        cx, cy = centers[tap_label]
        out += tap_at(c, cx, cy)
    return out


def meeting_open(c: Ctx) -> str:
    return meeting_doc(c, tap_label="회의 모드로 열기") + toc(c, ("지난 회의 확인", "결정사항", "액션 아이템"))


def meeting_note(c: Ctx) -> str:
    t = c.t
    out = meeting_doc(c, mode="meeting") + toc(c, ("지난 회의 확인", "결정사항", "액션 아이템"))
    plus = rise(c, 0.3, rect(BODY_R - 46, TOP + 190, 46, 18, rx=5, fill=t["surface"], stroke=t["line"])
                + text(BODY_R - 23, TOP + 203, "+ 메모", size=9.5, weight=700, fill=t["t1"], anchor="middle"))
    sel = mark(c, BODY_X, TOP + 262, 200, offset=0.8)
    floating = rise(c, 1.2, rect(BODY_X + 60, TOP + 274, 130, 22, rx=6, fill=t["t1"])
                    + text(BODY_X + 125, TOP + 289, "+ 선택 영역에 메모", size=10, weight=700, fill=t["bg"], anchor="middle"))
    return out + plus + sel + floating + tap_at(c, BODY_X + 125, TOP + 285)


def memo_form(c: Ctx, x) -> str:
    t = c.t
    kinds = ""
    kx = x + 12
    for kind in ("note", "decision", "action"):
        name = {"note": "일반", "decision": "결정", "action": "액션"}[kind]
        w = text_width(name, 10) + 18
        on = kind == "action"
        kinds += (rect(kx, TOP + 50, w, 20, rx=10, fill=t["action"] if on else t["surface2"])
                  + text(kx + w / 2, TOP + 64, name, size=10, weight=800, fill="#ffffff" if on else t["t2"], anchor="middle"))
        kx += w + 4
    fields = [("메모 내용", "배포 체크리스트 정리"), ("담당자", "이서준"), ("기한", "10/31")]
    rows = "".join(text(x + 12, TOP + 92 + i * 40, name, size=9, weight=700, fill=t["t3"])
                   + rect(x + 12, TOP + 98 + i * 40, VR - x - 26, 20, rx=5, fill=t["surface"], stroke=t["line"])
                   + text(x + 20, TOP + 112 + i * 40, value, size=10, fill=t["t1"]) for i, (name, value) in enumerate(fields))
    return kinds + rise(c, 0.5, rows)


def meeting_kind(c: Ctx) -> str:
    x = VR - 196
    base = meeting_doc(c, mode="meeting") + mark(c, BODY_X, TOP + 262, 200, kind="action")
    return base + panel(c, "메모", memo_form(c, x)) + tap_at(c, x + 115, TOP + 60, 0.4)


def meeting_summary(c: Ctx) -> str:
    t = c.t
    x = VR - 196
    out = []
    y = TOP + 52
    sections = [("결정사항", "decision", "배포는 11/3 화요일"), ("액션 아이템", "action", "배포 체크리스트 정리"),
                ("메모", "note", "모니터링 대시보드 확인")]
    for i, (name, kind, value) in enumerate(sections):
        _, b = badge(c, x + 12, y + 10, kind)
        out.append(rise(c, 0.2 + i * 0.15, text(x + 12, y, name, size=10.5, weight=800, fill=t["t1"]) + b
                        + text(x + 52, y + 22, fit(value, 10, VR - x - 66), size=10, fill=t["t1"])
                        + (text(x + 12, y + 40, "담당자 이서준 · 기한 10/31", size=9, fill=t["t3"]) if kind == "action" else "")))
        y += 60 if kind == "action" else 46
    buttons = ""
    by = VB - 82
    for i, name in enumerate((".md로 내보내기", "메모 포함 HTML로 내보내기", "가져오기")):
        _, svg = button(x + 12, by + i * 25, name, t, h=21, size=9.5, w=VR - x - 26)
        buttons += svg
    base = meeting_doc(c, mode="meeting") + mark(c, BODY_X, TOP + 262, 200, kind="action")
    return base + panel(c, "회의 정리", "".join(out) + rise(c, 0.7, buttons)) + tap_at(c, x + 85, by + 10)


# ── 5. 발표 화면 ───────────────────────────────────────────

def present_slide(c: Ctx, *, n=2, memo=False) -> str:
    t = c.t
    right = VR - (196 if memo else 0)
    titles = {1: "1. 지난 회의 확인", 2: "2. 결정사항"}
    out = [rect(VX, VY, VIEW["w"], VIEW["h"], rx=0, fill=t["bg"]),
           text(VX + 20, VY + 24, "10월 정기 회의", size=11, weight=700, fill=t["t2"]),
           text(right - 16, VY + 24, "10:12", size=11, fill=t["t3"], anchor="end"),
           rise(c, 0.2, heading(c, VX + 40, VY + 96, titles.get(n, "3. 액션 아이템"), 26)),
           rise(c, 0.4, "".join(f'<circle cx="{VX + 46}" cy="{VY + 138 + i * 34}" r="3.5" fill="{t["accent"]}"/>'
                                + rect(VX + 60, VY + 133 + i * 34, w, 10, rx=5, fill=t["line_strong"])
                                for i, w in enumerate((260, 220, 180))))]
    nav_y = VB - 40
    out.append(rect(VX, nav_y - 6, right - VX, 3, rx=0, fill=t["surface2"])
               + rect(VX, nav_y - 6, (right - VX) * n / 5, 3, rx=0, fill=t["accent"]))
    x = VX + 16
    for name in ("이전(←)", f"{n} / 5", "다음(→)", "메모 보기(N)", "발표 끝내기(Esc)"):
        if "/" in name:
            out.append(text(x + 2, nav_y + 16, name, size=10.5, weight=700, fill=t["t2"]))
            x += text_width(name, 10.5) + 10
            continue
        w, svg = button(x, nav_y + 2, name, t, h=22, size=10)
        out.append(svg)
        x += w + 6
    return "".join(out)


def present_open(c: Ctx) -> str:
    return meeting_doc(c, tap_label="발표하기") + toc(c, ("지난 회의 확인", "결정사항", "액션 아이템"))


def present_next(c: Ctx) -> str:
    keys = "".join(pop(c, 1.0 + i * 0.15, chip(VR - 150 + i * 46, VY + 46, k, c.t, size=11, h=24)[1])
                   for i, k in enumerate(("→", "Space")))
    return present_slide(c, n=2) + keys + tap_at(c, VX + 158, VB - 27)


def present_memo(c: Ctx) -> str:
    t = c.t
    x = VR - 196
    _, b = badge(c, x + 12, TOP + 50, "decision")
    body = rise(c, 0.4, b + text(x + 52, TOP + 62, "배포는 11/3 화요일", size=10, fill=t["t1"])
                + text(x + 12, TOP + 92, "2. 결정사항에 단 메모", size=9, fill=t["t3"]))
    return present_slide(c, n=2, memo=True) + panel(c, "메모", body) + tap_at(c, VX + 230, VB - 27)


def present_end(c: Ctx) -> str:
    keys = pop(c, 0.8, chip(VR - 70, VY + 46, "Esc", c.t, size=11, h=24)[1])
    return present_slide(c, n=3) + keys + tap_at(c, VX + 340, VB - 27)


# ── 6. 근거 미리보기·근거 모음 ──────────────────────────────

LINK = "보안규정 제12조"


def evidence_doc(c: Ctx, *, scrolled=False) -> str:
    t = c.t
    x = BODY_X
    if scrolled:
        return (topbar(c) + tree(c, selected="2026-10-27-정기회의.md")
                + heading(c, x, TOP + 30, "근거 모음", 15) + text(x, TOP + 48, "근거 2건", size=10, fill=t["t2"]))
    lead = "회의록은 5년 보관한다는 "
    body = (heading(c, x, TOP + 30, "10월 정기 회의") + heading(c, x, TOP + 62, "2. 결정사항", 12)
            # 링크가 글줄 안에 이어지도록 한 text 안의 tspan으로 쓴다(글자 폭 근사로 띄우면 틈이 생긴다).
            + f'<text x="{x}" y="{TOP + 84}" font-size="11" fill="{t["t1"]}">{lead}<tspan font-weight="700" '
              f'fill="{t["accent"]}" text-decoration="underline">{LINK}</tspan>을 따른다.</text>'
            + lines(x, TOP + 100, (250, 200, 140), t))
    return topbar(c) + tree(c, selected="2026-10-27-정기회의.md") + body + toc(c, ("결정사항", "근거 모음"))


def link_x() -> float:
    return BODY_X + text_width("회의록은 5년 보관한다는 ", 11) + text_width(LINK, 11) / 2


def evidence_hover(c: Ctx) -> str:
    return evidence_doc(c) + tap_at(c, link_x(), TOP + 80, 0.8)


def evidence_tip(c: Ctx) -> str:
    t = c.t
    x, y, w = BODY_X + 30, TOP + 98, 250
    tip = rise(c, 0.3, rect(x, y, w, 104, rx=8, fill=t["surface"], stroke=t["line"])
               + text(x + 12, y + 20, "규정/보안규정.md · 제12조 (회의록 보관)", size=9.5, weight=800, fill=t["t2"])
               + mark(c, x + 12, y + 42, 140, offset=0.5) + text(x + 12, y + 42, "① 회의록은 5년간 보관한다.", size=10.5, fill=t["t1"])
               + lines(x + 12, y + 54, (220, 180), t, gap=12, h=5)
               + text(x + 12, y + 92, "누르면 근거 모음으로 이동합니다", size=9.5, fill=t["t3"]))
    return evidence_doc(c) + tip


def evidence_list(c: Ctx) -> str:
    t = c.t
    x = BODY_X
    out = [evidence_doc(c, scrolled=True)]
    items = [(LINK, "① 회의록은 5년간 보관한다.", "2. 결정사항"), ("동기화.md · 배포 절차", "배포 전에 체크리스트를 확인한다.", "3. 액션 아이템")]
    for i, (name, quote, cite) in enumerate(items):
        y = TOP + 62 + i * 112
        _, open_btn = button(x + 12, y + 70, "원문 열기", t, h=20, size=9.5)
        out.append(rise(c, 0.2 + i * 0.2, rect(x, y, VR - x - 16, 100, rx=8, fill=t["surface"], stroke=t["line"])
                        + text(x + 12, y + 20, name, size=11, weight=800, fill=t["t1"])
                        + rect(x + 12, y + 30, 3, 18, rx=1.5, fill=t["accent"])
                        + text(x + 22, y + 43, quote, size=10.5, fill=t["t1"])
                        + text(x + 12, y + 62, f"인용한 곳: {cite}", size=9.5, fill=t["t2"]) + open_btn))
    return "".join(out) + tap_at(c, x + 50, TOP + 142)


def evidence_source(c: Ctx) -> str:
    t = c.t
    x = BODY_X
    body = (heading(c, x, TOP + 30, "보안규정") + heading(c, x, TOP + 62, "제11조 (문서 등급)", 12)
            + lines(x, TOP + 74, (240, 190), t)
            + rise(c, 0.3, rect(x - 6, TOP + 100, BODY_R - x + 12, 66, rx=6, fill="none", stroke=t["accent"],
                                extra=' stroke-width="2"'))
            + heading(c, x, TOP + 120, "제12조 (회의록 보관)", 12)
            + text(x, TOP + 140, "① 회의록은 5년간 보관한다.", size=11, fill=t["t1"]) + lines(x, TOP + 150, (220,), t))
    return topbar(c) + tree(c, selected="보안규정.md") + body + toc(c, ("제11조", "제12조", "제13조"), 1)


# ── 7. 슬라이드(Marp) ───────────────────────────────────────

SLIDE_BUTTONS = ("슬라이드 미리보기", "PDF 만들기", "출판…", "내려받기")


def slide_doc(c: Ctx) -> tuple[str, dict]:
    t = c.t
    x = BODY_X
    bar, centers = toolbar(c, x, TOP + 40, SLIDE_BUTTONS, primary="슬라이드 미리보기")
    front = rise(c, 0.1, rect(x, TOP + 74, 150, 52, rx=6, fill=t["surface2"], stroke=t["line"])
                 + "".join(text(x + 10, TOP + 88 + i * 13, line, size=9.5, fill=t["t2"], mono=True)
                           for i, line in enumerate(("---", "marp: true", "theme: gaia"))))
    body = bar + front + heading(c, x, TOP + 150, "3분기 운영 보고", 13) + lines(x, TOP + 162, (230, 180, 120), t)
    return (topbar(c) + tree(c, selected="분기보고.md", open_dirs=("발표",)) + heading(c, x, TOP + 30, "분기 보고")
            + body), centers


def slides_preview(c: Ctx) -> str:
    t = c.t
    doc, centers = slide_doc(c)
    x, y = centers["슬라이드 미리보기"]
    tab = rise(c, 1.4, rect(VX + 150, TOP + 110, 250, 150, rx=8, fill=t["accent"])
               + text(VX + 170, TOP + 150, "3분기 운영 보고", size=16, weight=800, fill=t["on_accent"])
               + text(VX + 170, TOP + 172, "새 탭의 슬라이드 · 1 / 6", size=10, fill=t["on_accent"]))
    return doc + tab + toast(c, "슬라이드를 만드는 중…", 0.8) + tap_at(c, x, y, 0.2)


def slides_pdf(c: Ctx) -> str:
    doc, centers = slide_doc(c)
    x, y = centers["PDF 만들기"]
    return (doc + tap_at(c, x, y, 0.2) + toast(c, "PDF 만드는 중…", 0.6)
            + toast(c, "PDF를 내려받았습니다", 2.0, kind="success"))


def slides_dialog(c: Ctx) -> str:
    t = c.t
    doc, _ = slide_doc(c)
    x, y, w = VX + 104, TOP + 60, 320
    fields = [("저장소", "team-slides (main)"), ("파일", "slides/분기보고.html")]
    rows = "".join(text(x + 20, y + 100 + i * 34, name, size=10, weight=700, fill=t["t3"])
                   + text(x + 70, y + 100 + i * 34, value, size=10.5, fill=t["t1"], mono=i == 1)
                   for i, (name, value) in enumerate(fields))
    _, cancel = button(x + w - 118, y + 186, "취소", t, h=24, size=10.5, w=48)
    _, ok = button(x + w - 64, y + 186, "출판", t, variant="filled", h=24, size=10.5, w=44)
    dialog = rise(c, 0.3, rect(VX, TOP, VIEW["w"], VIEW["h"] - BAR, rx=0, fill=t["t1"], extra=' fill-opacity=".18"')
                  + rect(x, y, w, 226, rx=10, fill=t["surface"], stroke=t["line"])
                  + text(x + 20, y + 32, "공유 저장소에 출판할까요?", size=14, weight=800, fill=t["t1"])
                  + text(x + 20, y + 54, "출판하면 팀이 함께 보는 슬라이드 저장소에", size=10, fill=t["t2"])
                  + text(x + 20, y + 68, "바로 커밋됩니다.", size=10, fill=t["t2"]) + rows + cancel + ok)
    return doc + dialog + tap_at(c, x + w - 42, y + 198)


def slides_done(c: Ctx) -> str:
    doc, _ = slide_doc(c)
    return (doc + toast(c, "출판하는 중…", 0.1) + toast(c, "출판했습니다", 1.0, action="열기", kind="success")
            + tap_at(c, VR - 41, VB - 31, 2.0))


# ── 메뉴 정의 ──────────────────────────────────────────────

PAGES = [
    Page(
        id="browse", title="저장소·문서 찾아가기", url="/view/",
        lead=["Git 저장소의 Markdown 문서를 웹에서 엽니다.", "저장소 목록에서 파일 트리·목차로 찾아갑니다."],
        steps=[
            Step("저장소 고르기", "`/view/` 저장소 목록에서 카드를 누릅니다", browse_list),
            Step("파일 트리에서 문서 고르기", "왼쪽 트리에서 폴더를 펼치고 문서를 누릅니다", browse_home, f"/view/{REPO}"),
            Step("본문 읽기", "Mermaid 그림과 코드 강조도 그대로 보입니다", browse_doc, DOC_URL),
            Step("목차로 이동", "오른쪽 `이 문서` 목차를 누르거나 `t`로 엽니다", browse_toc, DOC_URL),
        ],
        tips=[
            "화면이 넓으면(1100px 이상) 파일 트리·본문·목차 3단으로, 좁으면 목차가 왼쪽 `이 문서` 탭으로 들어갑니다. 767px 이하에서는 ☰ `메뉴 열기`로 트리를 엽니다.",
            "파일 트리와 본문 사이 경계를 끌면 트리 너비가 바뀝니다(200~600px). 경계에 초점을 두고 ←/→(Shift는 크게)·`Home`·`End`로도 바꾸고, 두 번 누르면 기본 너비로 돌아갑니다. 정한 너비는 이 브라우저에 남습니다.",
            "상단 바 `저장소`·`브랜치`에서 다른 저장소나 브랜치로 바로 옮깁니다. 기본 브랜치에는 `(기본)`이 붙습니다.",
            "저장소를 처음 받는 동안에는 `저장소 받는 중`, 동기화에 실패하면 `동기화 실패`가 상단 바에 보입니다.",
            "`최근 본 문서`는 이 브라우저에만 남습니다.",
            "없는 문서 주소로 들어오면 `비슷한 이름`의 문서와 `저장소 처음으로`·`저장소 목록` 단추를 보여 줍니다.",
        ],
    ),
    Page(
        id="search", title="문서 검색", url=DOC_URL,
        lead=["파일 이름과 본문을 함께 찾습니다.", "여러 저장소의 본문은 Git에서 바로 검색합니다."],
        steps=[
            Step("검색 상자 열기", "`⌘K`·`/`를 누르거나 `문서 검색`을 누릅니다", search_open),
            Step("두 글자 이상 입력", "`파일`과 `본문` 결과가 나뉘어 바로 뜹니다", search_type),
            Step("결과 고르기", "↑↓로 옮기고 Enter로 엽니다. Esc는 닫기", search_pick),
            Step("전체 결과 보기", "‘검색어’ 전체 결과 보기에서 저장소별로 봅니다", search_page, "/view/search?q=회의록"),
        ],
        tips=[
            "검색어는 두 글자 이상, 100글자 이하로 한 줄만 입력합니다.",
            "전체 결과 화면에서 `모든 저장소`를 한 저장소로 바꿔 좁힐 수 있습니다. 결과마다 몇 번째 줄(`12행`)인지 보여 줍니다.",
            "파일 이름만 빨리 거르려면 파일 트리 위 `파일 이름으로 좁히기`를 씁니다.",
            "검색 주소(`/view/search?q=…`)를 그대로 보내면 같은 결과를 함께 봅니다.",
        ],
    ),
    Page(
        id="document", title="문서 보기·내려받기", url=DOC_URL,
        lead=["오프라인에서도 그림·코드 강조가 그대로 보입니다.", "테마를 고르고 원본·HTML·PDF로 내려받습니다."],
        steps=[
            Step("테마 바꾸기", "상단 바 `테마`에서 7가지 중 고릅니다", document_theme),
            Step("`내려받기` 열기", "`원본`·`HTML`·`PDF` 중 하나를 고릅니다", document_download),
            Step("브랜치 바꾸기", "상단 바 `브랜치`에서 다른 브랜치의 문서를 봅니다", document_branch, DOC_URL + "?ref=release"),
            Step("단축키 보기", "`?`를 누르면 `단축키` 안내가 열립니다", document_keys),
        ],
        tips=[
            "고른 테마는 이 브라우저에 기억됩니다. 주소에 `?theme=github-dark`처럼 붙이면 그 테마로 엽니다.",
            "`HTML`은 지금 보이는 문서를 HTML 파일 하나로, `PDF`는 Marp 서비스가 만든 PDF로 받습니다. Marp가 꺼진 배포에는 `PDF`가 없습니다.",
            "다이어그램은 Viewer가 아니라 내부 Draw.io(`/drawio/`)에서 그리고, `.drawio` 파일로 저장해 Git에 올립니다. `.drawio` 파일은 Viewer에서 열리지 않으니 문서에는 PNG·SVG로 내보낸 그림을 넣습니다.",
            "단축키: `⌘K`·`Ctrl K`·`/` 문서 검색 · `t` 이 문서 목차 열기 · `?` 이 안내 열기 · `Esc` 열린 창·메뉴 닫기",
        ],
    ),
    Page(
        id="meeting", title="회의 모드", url=MEETING_URL,
        lead=["회의 문서에 메모·형광펜을 달며 회의합니다.", "결정과 액션 아이템은 `회의 정리`에 모입니다."],
        steps=[
            Step("`회의 모드로 열기`", "회의 문서 위의 단추를 누릅니다", meeting_open),
            Step("메모 달기", "`+ 메모`나 글을 골라 `+ 선택 영역에 메모`", meeting_note, MEETING_URL + "?mode=meeting"),
            Step("결정·액션 고르기", "`일반`·`결정`·`액션`과 담당자·기한을 적습니다", meeting_kind, MEETING_URL + "?mode=meeting"),
            Step("`회의 정리`와 내보내기", "모아 보고 `.md로 내보내기`로 받아 전합니다", meeting_summary, MEETING_URL + "?mode=meeting"),
        ],
        tips=[
            "회의 문서는 Front Matter에 `synctx.type: meeting`과 `synctx.meeting`(id·title·starts_at·channel)이 있는 문서입니다. 문서 머리 카드에 일시·발표자·채널·안건과 `시작 전`·`진행 중 · N분 지남 · M분 남음`·`끝남`이 보입니다.",
            "메모는 이 브라우저(localStorage)에만 저장되고 다른 사람과 공유되지 않습니다. 함께 보려면 `.md로 내보내기`로 받은 파일을 전하고, 받은 사람은 `가져오기`로 합칩니다.",
            "`메모 포함 HTML로 내보내기`는 메모와 렌더링 라이브러리를 모두 담은 읽기 전용 HTML 파일 하나를 만듭니다.",
            "오른쪽 아래 `메모 N` 단추로 패널을 열고 닫습니다. `Esc`로 닫고, 패널 경계는 ←/→로 너비를 바꿉니다.",
            "처음 들어오면 `회의 모드 안내`가 뜹니다. `다시 보지 않기`를 눌러도 `사용법`이나 `?`의 `회의 모드 안내 보기`로 다시 볼 수 있습니다.",
        ],
    ),
    Page(
        id="present", title="발표 화면", url=MEETING_URL,
        lead=["회의 문서를 `h2` 제목마다 한 장씩 넘깁니다.", "내 회의 메모를 곁에 띄워 놓을 수 있습니다."],
        steps=[
            Step("`발표하기`", "회의 문서 위의 단추로 발표 화면을 엽니다", present_open),
            Step("장 넘기기", "`다음(→)`·Space로 다음, `이전(←)`으로 앞 장", present_next, MEETING_URL + "?mode=present#slide-2"),
            Step("메모 보기", "`메모 보기(N)`이나 N으로 내 메모를 곁에 엽니다", present_memo, MEETING_URL + "?mode=present#slide-2"),
            Step("발표 끝내기", "`발표 끝내기(Esc)`로 문서로 돌아갑니다", present_end, MEETING_URL + "?mode=present#slide-3"),
        ],
        tips=[
            "Home·End로 처음·마지막 장으로 갑니다. PageDown·PageUp도 다음·이전입니다.",
            "주소 끝의 `#slide-3`처럼 장 번호가 남아 새로고침해도 같은 장에서 이어집니다.",
            "발표 화면은 회의 문서에서만 열립니다. 일반 문서는 `발표 화면은 회의 문서에서만 쓸 수 있습니다`라고 알려 줍니다.",
            "슬라이드 파일(PDF·HTML)이 필요하면 [슬라이드](slides.md)를 쓰세요.",
        ],
    ),
    Page(
        id="evidence", title="근거 미리보기·근거 모음", url=MEETING_URL,
        lead=["회의 문서가 인용한 조문을 그 자리에서 봅니다.", "문서 끝 `근거 모음`에서 한꺼번에 확인합니다."],
        steps=[
            Step("근거 링크에 올리기", "근거 링크에 마우스를 올리거나 Tab으로 갑니다", evidence_hover),
            Step("발췌 확인", "말풍선에 원문 조문·절이 나옵니다. Esc로 닫기", evidence_tip),
            Step("`근거 모음` 보기", "링크를 누르면 문서 끝 근거 모음으로 이동합니다", evidence_list, MEETING_URL),
            Step("`원문 열기`", "`인용한 곳`을 보고 원래 문서의 그 절을 엽니다", evidence_source, f"/view/{REPO}/규정/보안규정.md"),
        ],
        tips=[
            "다른 저장소의 문서는 Front Matter의 `synctx.meeting.references` 접두어로 가리킵니다. 저장소를 찾지 못하면 `근거 저장소를 찾지 못했습니다`가 보입니다.",
            "`메모 포함 HTML로 내보내기`로 받은 파일에서도 근거 말풍선과 근거 모음이 동작합니다.",
            "근거 모음은 문서별로 묶이고, 각 항목에 `인용한 곳`이 붙습니다.",
        ],
    ),
    Page(
        id="slides", title="슬라이드(Marp)", url=f"/view/{REPO}/발표/분기보고.md",
        lead=["`marp: true` 문서를 슬라이드로 보고", "PDF로 받거나 팀 슬라이드 저장소에 출판합니다."],
        steps=[
            Step("`슬라이드 미리보기`", "새 탭에서 슬라이드로 봅니다", slides_preview),
            Step("`PDF 만들기`", "`PDF 만드는 중…` 뒤 PDF를 내려받습니다", slides_pdf),
            Step("`출판…`", "출판할 저장소와 파일을 확인하고 `출판`을 누릅니다", slides_dialog),
            Step("출판 확인", "`출판했습니다`의 `열기`로 Slides에서 봅니다", slides_done),
        ],
        tips=[
            "Front Matter에 `marp: true`가 있는 문서에만 단추가 보입니다. 테마는 `default`·`gaia`·`uncover`입니다.",
            "`출판…`은 운영자가 출판 저장소를 설정한 배포에만 있습니다. 권한이 없으면 `권한이 없습니다`가 뜹니다.",
            "PDF를 만들지 못하면 오류 알림의 `HTML로 받기`로 HTML 파일을 대신 받습니다.",
        ],
    ),
]

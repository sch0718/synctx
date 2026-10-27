"""README 맨 위의 소개 그림. 장면 넷(작성 → 반입 → 열람·회의 → 동기화)이 이어서 돈다."""
from __future__ import annotations

from .kit import at, button, chip, lines, motion_css, rect, svg_document, text, text_width

W, H = 960, 440
SCENE = 4.2  # 장면 하나의 길이(초)
STEPS = [
    ("외부망에서 작성", "AI와 함께 Markdown·Front Matter로"),
    ("Git으로 반입", "단방향 동기화, Git이 원본"),
    ("Viewer에서 읽고 회의", "검색·회의 메모·발표·근거"),
    ("내부 도구로 동기화", "XWiki 문서·OpenProject 작업"),
]
P = {"x": 400, "y": 56, "w": 512}
# 장면 1의 회의 문서. Viewer가 받아들이는 Front Matter(id·title·starts_at(시간대 포함)·channel 필수)를 그대로 쓴다.
MEETING_EXAMPLE = ["---", "synctx:", "  type: meeting", "  route: xwiki", "  meeting:", "    id: platform-2026-10-27",
                   "    title: 10월 정기 회의", "    starts_at: 2026-10-27T10:00:00+09:00", "    channel: platform-weekly",
                   "---", "# 10월 정기 회의"]  # 오른쪽 장면 영역
delay = at(SCENE)


def _rise(scene: int, offset: float, body: str) -> str:
    return f'<g class="rise" style="{delay(scene, offset)}">{body}</g>'


def _pop(scene: int, offset: float, body: str) -> str:
    return f'<g class="pop" style="{delay(scene, offset)}">{body}</g>'


def _window(t, x, y, w, h, title) -> str:
    dots = "".join(f'<circle cx="{x + 16 + i * 12}" cy="{y + 14}" r="4" fill="{t["t3"]}" fill-opacity=".55"/>' for i in range(3))
    return (rect(x, y, w, h, rx=12, fill=t["surface"], stroke=t["line"]) + rect(x, y, w, 28, rx=12, fill=t["window"])
            + rect(x, y + 16, w, 12, rx=0, fill=t["window"]) + dots
            + text(x + 58, y + 18, title, size=11, weight=600, fill=t["t2"]))


def _scene_write(t) -> str:
    x, y = P["x"], P["y"]
    source = MEETING_EXAMPLE
    code = "".join(_rise(0, 0.2 + i * 0.1, text(x + 20, y + 54 + i * 19, line, size=11.5, fill=t["accent"] if i < 10 else t["t1"],
                                                    weight=700 if line.startswith("#") else 400, mono=True))
                   for i, line in enumerate(source))
    chips, cx = "", x + 350
    for i, name in enumerate(("Claude Code", "Codex", "Cursor")):
        w, svg = chip(cx, y + 64 + i * 36, name, t, on=i == 0, size=12, h=28)
        chips += _pop(0, 1.2 + i * 0.2, svg)
    return (f'<g class="sc s0" style="{delay(0)}">{_window(t, x, y, 330, 280, "회의/2026-10-27-정기회의.md")}{code}{chips}'
            + _rise(0, 2.0, text(x + 350, y + 196, "AI가 쓴 Markdown이", size=13, fill=t["t2"])
                    + text(x + 350, y + 216, "팀의 공통 컨텍스트가 됩니다", size=13, fill=t["t2"])) + "</g>")


def _scene_import(t) -> str:
    x, y = P["x"], P["y"]

    def box(bx, title, sub):
        return (rect(bx, y + 70, 190, 120, rx=14, fill=t["surface"], stroke=t["line"])
                + text(bx + 20, y + 104, title, size=16, weight=800, fill=t["t1"])
                + text(bx + 20, y + 128, sub, size=12, fill=t["t2"])
                + lines(bx + 20, y + 146, (130, 100), t, gap=12, h=6))

    docs = "".join(f'<g class="rise" style="{delay(1, 0.6 + i * 0.35)}">{rect(x + 222 + i * 22, y + 112, 16, 20, rx=3, fill=t["accent_soft"], stroke=t["accent"])}</g>'
                   for i in range(3))
    flow = (f'<path class="flow" style="{delay(1)}" d="M{x + 196} {y + 130} H{x + 316}" stroke="{t["accent"]}" '
            f'stroke-width="3" fill="none"/>'
            f'<path d="M{x + 308} {y + 122} l10 8 -10 8" fill="none" stroke="{t["accent"]}" stroke-width="3"/>')
    return (f'<g class="sc s1" style="{delay(1)}">{_rise(1, 0.1, box(x, "외부망", "GitHub · AI 개발 도구"))}{flow}{docs}'
            + _rise(1, 0.3, box(x + 322, "폐쇄망", "내부 Git · K8s 서비스"))
            + _rise(1, 1.4, text(x, y + 232, "외부 → 내부 반입은 Git 동기화로 거의 자동입니다.", size=14, weight=700, fill=t["t1"]))
            + _rise(1, 1.7, text(x, y + 256, "Git이 원본이고, 내부 도구는 거기서 만든 파생 데이터입니다.", size=13, fill=t["t2"]))
            + "</g>")


def _scene_view(t) -> str:
    x, y = P["x"], P["y"]
    _, open_btn = button(x + 150, y + 74, "회의 모드로 열기", t, variant="filled", h=24, size=11)
    _, present = button(x + 262, y + 74, "발표하기", t, h=24, size=11)
    tree = "".join(text(x + 16, y + 66 + i * 20, name, size=11, weight=700 if i == 1 else 500, fill=t["accent"] if i == 1 else t["t1"])
                   for i, name in enumerate(("▾ 회의", "· 정기회의.md", "▸ 설계", "▸ 규정")))
    hl = (f'<g class="fade" style="{delay(2, 1.0)}">{rect(x + 150, y + 172, 180, 14, rx=2, fill=t["hl"])}'
          f'<path d="M{x + 150} {y + 186} h180" stroke="{t["decision"]}" stroke-width="2"/></g>')
    memo = _rise(2, 1.5, rect(x + 352, y + 120, 150, 92, rx=10, fill=t["surface"], stroke=t["line"])
                 + chip(x + 364, y + 132, "결정", t, size=10, h=18, fill=t["decision"], color="#ffffff")[1]
                 + text(x + 364, y + 170, "배포는 11/3 화요일", size=11.5, weight=700, fill=t["t1"])
                 + text(x + 364, y + 190, "회의 정리에 모입니다", size=10.5, fill=t["t2"]))
    search = _pop(2, 0.4, rect(x + 300, y + 36, 190, 22, rx=6, fill=t["surface2"], stroke=t["accent"])
                  + text(x + 312, y + 51, "⌕ 문서 검색", size=11, fill=t["t2"]) + text(x + 476, y + 51, "⌘K", size=10, weight=700, fill=t["t2"], anchor="end"))
    return (f'<g class="sc s2" style="{delay(2)}">{_window(t, x, y, P["w"], 280, "/view/team-docs/회의/정기회의.md")}'
            + rect(x, y + 28, 136, 252, rx=0, fill=t["surface2"]) + tree + search
            + text(x + 150, y + 64, "10월 정기 회의", size=17, weight=800, fill=t["t1"]) + open_btn + present
            + lines(x + 150, y + 118, (180, 150), t) + text(x + 150, y + 160, "2. 결정사항", size=12, weight=800, fill=t["t1"])
            + text(x + 150, y + 182, "배포는 11/3 화요일에 한다", size=12, fill=t["t1"]) + hl + memo
            + lines(x + 150, y + 206, (190, 160, 120), t) + "</g>")


def _scene_sync(t) -> str:
    x, y = P["x"], P["y"]
    md = _rise(3, 0.1, rect(x, y + 96, 120, 120, rx=12, fill=t["surface"], stroke=t["line"])
               + text(x + 60, y + 144, "Markdown", size=13, weight=800, fill=t["t1"], anchor="middle")
               + text(x + 60, y + 166, "route: both", size=11, fill=t["accent"], anchor="middle", mono=True))
    arrows = "".join(f'<path class="flow" style="{delay(3)}" d="M{x + 124} {y + 156} C{x + 170} {y + 156} {x + 170} {ty} {x + 214} {ty}" '
                     f'fill="none" stroke="{t["accent"]}" stroke-width="2.5"/>' for ty in (y + 92, y + 224))
    wiki = _rise(3, 0.5, rect(x + 220, y + 40, 292, 104, rx=12, fill=t["surface"], stroke=t["line"])
                 + text(x + 238, y + 66, "XWiki", size=12, weight=800, fill=t["t2"])
                 + text(x + 238, y + 90, "10월 정기 회의", size=15, weight=800, fill=t["t1"])
                 + lines(x + 238, y + 104, (220, 180), t, gap=12, h=6))
    rows = [("배포 체크리스트 정리", "진행 중"), ("모니터링 대시보드 확인", "신규"), ("보안 점검 결과 공유", "완료")]
    tasks = "".join(text(x + 238, y + 210 + i * 22, name, size=12, fill=t["t1"])
                    + chip(x + 496 - text_width(state, 10) - 20, y + 197 + i * 22, state, t, size=10, h=18, on=state == "진행 중")[1]
                    for i, (name, state) in enumerate(rows))
    op = _rise(3, 0.9, rect(x + 220, y + 164, 292, 120, rx=12, fill=t["surface"], stroke=t["line"])
               + text(x + 238, y + 188, "OpenProject", size=12, weight=800, fill=t["t2"]) + tasks)
    return (f'<g class="sc s3" style="{delay(3)}">{md}{arrows}{wiki}{op}'
            + _rise(3, 1.6, text(x, y + 316, "Front Matter의 type·route로 문서와 작업을 알아서 나눕니다.", size=13, fill=t["t2"]))
            + "</g>")


def render_hero(t: dict[str, str]) -> str:
    def step_y(i: int) -> int:
        return 212 + i * 52

    steps = []
    for i, (title, sub) in enumerate(STEPS):
        y = step_y(i)
        active = f' class="sc s{i}" style="{delay(i)}"'
        steps.append(
            f'<g>{rect(36, y - 23, 320, 48, rx=12, fill=t["accent_soft"], extra=active)}'
            f'<circle cx="60" cy="{y}" r="12" fill="none" stroke="{t["t3"]}" stroke-width="1.5"/>'
            f'{text(60, y + 4.5, i + 1, size=12, weight=800, fill=t["t2"], anchor="middle")}'
            f'<g class="sc s{i}" style="{delay(i)}"><circle cx="60" cy="{y}" r="12" fill="{t["accent"]}"/>'
            f'{text(60, y + 4.5, i + 1, size=12, weight=800, fill=t["on_accent"], anchor="middle")}</g>'
            f'{text(84, y - 3, title, size=16, weight=800, fill=t["t1"])}{text(84, y + 16, sub, size=12.5, fill=t["t2"])}</g>')
    logo = (f'<circle cx="52" cy="78" r="7" fill="none" stroke="{t["accent"]}" stroke-width="3"/>'
            f'<path d="M52 58 v9 m0 22 v9 M32 78 h9 m22 0 h9" stroke="{t["accent"]}" stroke-width="3" stroke-linecap="round"/>')
    body = (rect(1, 1, W - 2, H - 2, rx=24, fill=t["bg"], stroke=t["line"]) + logo
            + text(80, 88, "synctx", size=30, weight=800, fill=t["t1"])
            + text(36, 136, "외부에서 AI와 쓴 문서를", size=15, weight=600, fill=t["t2"])
            + text(36, 158, "폐쇄망 안에서 읽고, 회의하고, 동기화합니다", size=15, weight=600, fill=t["t2"])
            + "".join(steps) + "\n" + _scene_write(t) + "\n" + _scene_import(t) + "\n" + _scene_view(t) + "\n" + _scene_sync(t))
    return svg_document(
        w=W, h=H, title="synctx 소개",
        desc="synctx 소개: " + " → ".join(f"{i + 1}. {title}({sub})" for i, (title, sub) in enumerate(STEPS)),
        css=motion_css(scene_length=SCENE, scenes=len(STEPS)), body=body)

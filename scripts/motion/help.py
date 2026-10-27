"""메뉴별 도움말 그림과 글.

왼쪽에 단계 목록, 오른쪽에 브라우저 창 안의 Viewer 화면을 두고 단계마다 실제 화면 흐름을 보여 준다.
누르는 곳은 동그라미(`tap`)로 알리고, 다음 단계에서 그 결과 화면이 나온다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from .kit import at, esc, fit, motion_css, rect, svg_document, text, text_width

W, H = 960, 520
STEP = 3.8  # 단계 하나의 길이(초)

# 브라우저 창과 그 안쪽(Viewer 화면) 영역. 단계 화면은 help_pages.py에서 이 좌표로 그린다.
WIN = {"x": 404, "y": 28, "w": 528, "h": 464}
VIEW = {"x": 404, "y": 60, "w": 528, "h": 432}
GENERATED = ("<!-- 이 파일은 python3 scripts/build_motion.py로 생성합니다. "
             "문구는 scripts/motion/help_pages.py에서 고칩니다. -->")


@dataclass
class Ctx:
    t: dict[str, str]
    dark: bool
    scene: int

    def d(self, offset: float = 0) -> str:
        return at(STEP)(self.scene, offset)


@dataclass
class Step:
    title: str
    sub: str
    screen: Callable[[Ctx], str]
    url: str | None = None


@dataclass
class Page:
    id: str
    title: str
    lead: list[str]
    url: str
    steps: list[Step]
    tips: list[str] = field(default_factory=list)


def plain(value: str) -> str:
    """도움말 글의 `단추 이름`을 그림 글자로 바꾼다(그림에는 ‘단추 이름’)."""
    return re.sub(r"`([^`]+)`", r"‘\1’", value)


def _window(t: dict[str, str]) -> str:
    """브라우저 창 틀. 모든 단계가 함께 쓰므로 한 번만 그린다."""
    x, y, w = WIN["x"], WIN["y"], WIN["w"]
    dots = "".join(f'<circle cx="{x + 18 + i * 14}" cy="{y + 16}" r="4.5" fill="{t["t3"]}" fill-opacity=".55"/>'
                   for i in range(3))
    return (rect(x, y, w, WIN["h"], rx=14, fill=t["window"], stroke=t["line"]) + dots
            + rect(x + 66, y + 7, w - 82, 18, rx=9, fill=t["bg"])
            + f'<clipPath id="view"><path d="{_view_path()}"/></clipPath>' + f'<path d="{_view_path()}" fill="{t["bg"]}"/>')


def _view_path() -> str:
    """창 안쪽 영역. 아래 두 모서리를 창 틀처럼 둥글게 깎는다."""
    x, y, w, h, r = VIEW["x"], VIEW["y"], VIEW["w"], VIEW["h"], 13
    return f"M{x} {y} H{x + w} V{y + h - r} Q{x + w} {y + h} {x + w - r} {y + h} H{x + r} Q{x} {y + h} {x} {y + h - r} Z"


def _address(t: dict[str, str], url: str) -> str:
    return text(WIN["x"] + 78, WIN["y"] + 20, fit(url, 11, WIN["w"] - 110), size=11, fill=t["t2"])


def render_help(page: Page, t: dict[str, str], mode: str) -> str:
    delay = at(STEP)

    def step_y(i: int) -> int:
        return 262 + i * 56

    steps = []
    for i, s in enumerate(page.steps):
        y = step_y(i)
        active = f' class="sc s{i}" style="{delay(i)}"'
        steps.append(
            f'<g>{rect(28, y - 24, 356, 50, rx=12, fill=t["accent_soft"], extra=active)}'
            f'<circle cx="52" cy="{y}" r="12" fill="none" stroke="{t["t3"]}" stroke-width="1.5"/>'
            f'{text(52, y + 4.5, i + 1, size=12, weight=800, fill=t["t2"], anchor="middle")}'
            f'<g class="sc s{i}" style="{delay(i)}"><circle cx="52" cy="{y}" r="12" fill="{t["accent"]}"/>'
            f'{text(52, y + 4.5, i + 1, size=12, weight=800, fill=t["on_accent"], anchor="middle")}</g>'
            f'{text(74, y - 3, fit(plain(s.title), 15, 300), size=15, weight=800, fill=t["t1"])}'
            f'{text(74, y + 15, fit(plain(s.sub), 12, 304), size=12, fill=t["t2"])}</g>')

    screens = []
    for i, s in enumerate(page.steps):
        ctx = Ctx(t=t, dark=mode == "dark", scene=i)
        screens.append(f'<g class="sc s{i}" style="{delay(i)}">{_address(t, s.url or page.url)}'
                       f'<g clip-path="url(#view)">{s.screen(ctx)}</g></g>')

    lead = "".join(text(32, 150 + i * 22, fit(plain(line), 13.5, 360), size=13.5, weight=600, fill=t["t2"])
                   for i, line in enumerate(page.lead))
    body = (rect(1, 1, W - 2, H - 2, rx=24, fill=t["bg"], stroke=t["line"])
            + text(32, 64, "synctx 도움말", size=13, weight=700, fill=t["t3"])
            + text(32, 106, page.title, size=28, weight=800, fill=t["t1"])
            + lead + "".join(steps) + "\n" + _window(t) + "\n" + "\n".join(screens)
            + rect(WIN["x"], WIN["y"], WIN["w"], WIN["h"], rx=14, stroke=t["line"]))
    return svg_document(
        w=W, h=H, title=f"synctx 도움말 — {page.title}",
        desc=plain(f"{page.title} 사용법: " + " ".join(f"{i + 1}. {s.title}({s.sub})" for i, s in enumerate(page.steps))),
        css=motion_css(scene_length=STEP, scenes=len(page.steps)), body=body)


def _picture(page: Page) -> str:
    alt = plain(f"{page.title} 사용법: " + ", ".join(f"{i + 1}. {s.title}" for i, s in enumerate(page.steps)))
    alt = esc(alt).replace('"', "&quot;")
    return f"""<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="img/{page.id}-dark.svg">
    <img src="img/{page.id}-light.svg" width="960" alt="{alt}">
  </picture>
</p>"""


def render_help_markdown(page: Page) -> str:
    """메뉴 하나의 도움말 글(docs/help/<id>.md)."""
    steps = "\n".join(f"{i + 1}. **{s.title}** — {s.sub}" for i, s in enumerate(page.steps))
    tips = "\n".join(f"- {tip}" for tip in page.tips)
    lead = " ".join(page.lead)
    return f"""# {page.title}

{GENERATED}

{_picture(page)}

{lead}

## 이렇게 써요

{steps}

## 알아 두면 좋아요

{tips}

---

[도움말 모음](README.md) · [README](../../README.md)
"""


def render_help_index(pages: list[Page]) -> str:
    """도움말 모음(docs/help/README.md)."""
    rows = "\n".join("| [{}]({}.md) | {} |".format(p.title, p.id, " → ".join(s.title for s in p.steps)) for p in pages)
    return f"""# synctx 도움말

{GENERATED}

Viewer의 메뉴마다 실제 화면 흐름을 움직이는 그림으로 보여 줍니다. 서비스 소개는 [README](../../README.md)에 있습니다.

| 메뉴 | 이렇게 써요 |
| --- | --- |
{rows}

그림과 글은 `python3 scripts/build_motion.py`가 `scripts/motion/help_pages.py`로 만듭니다. 화면 문구가 바뀌면 그 파일을 고치고 다시 만드세요.
"""


def label_width(label: str, size: float) -> float:
    return text_width(label, size)

"""README·도움말의 움직이는 그림(애니메이션 SVG)이 함께 쓰는 그리기 도구.

GitHub는 SVG를 <img>로 보여 주므로 스크립트·웹 글꼴 없이 CSS 애니메이션만 쓴다.
landmark·gazua 저장소의 scripts/motion/kit.mjs를 Python으로 옮겼다.
"""
from __future__ import annotations

import re
from html import escape

FONT = ("-apple-system, BlinkMacSystemFont, 'Apple SD Gothic Neo', 'Malgun Gothic', "
        "'Noto Sans KR', 'Segoe UI', sans-serif")
MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"


def esc(value) -> str:
    return escape(str(value), quote=False)


def num(value: float) -> str:
    """좌표를 짧게 적는다(정수는 소수점 없이)."""
    return f"{value:.1f}".rstrip("0").rstrip(".") if isinstance(value, float) else str(value)


def at(scene_length: float):
    """장면 `scene`(0부터)이 시작하고 `offset`초 뒤에 움직임을 시작한다."""
    def delay(scene: int, offset: float = 0) -> str:
        return f"animation-delay:{scene * scene_length + offset:.2f}s"
    return delay


def text(x, y, value, *, size=14, weight=400, fill, anchor="start", cls="", style="", mono=False) -> str:
    # 글꼴은 style의 text 규칙보다 앞서도록 인라인 style로 준다. 앞 공백은 SVG가 접으므로 줄바꿈 없는 공백으로.
    if mono:
        style = f"font-family:{MONO.replace(chr(39), '')};{style}"
        value = str(value).replace(" ", "\u00a0")
    extra = (f' class="{cls}"' if cls else "") + (f' style="{style}"' if style else "")
    family = ""
    # 기본값(굵기 400, 왼쪽 정렬)은 적지 않아 파일을 줄인다.
    attrs = (f' font-weight="{weight}"' if weight != 400 else "") + (f' text-anchor="{anchor}"' if anchor != "start" else "")
    return f'<text x="{num(x)}" y="{num(y)}" font-size="{num(size)}" fill="{fill}"{attrs}{family}{extra}>{esc(value)}</text>'


def text_width(label, size: float) -> float:
    """글자 폭 근사(한글 1em, 이모지 1.2em, 그 밖 0.6em)."""
    width = 0.0
    for ch in str(label):
        if re.match(r"[ㄱ-ㆎ가-힣]", ch):
            width += size
        elif ord(ch) >= 0x1F000 or ch in "⌘✓✕★":
            width += size * 1.1
        elif ch != "️":
            width += size * 0.6
    return round(width)


def fit(label, size: float, max_w: float) -> str:
    """폭 `max_w`를 넘으면 뒤를 자르고 …를 붙인다."""
    out = str(label)
    if text_width(out, size) <= max_w:
        return out
    while len(out) > 1 and text_width(out + "…", size) > max_w:
        out = out[:-1]
    return out.rstrip() + "…"


def rect(x, y, w, h, *, rx=8, fill="none", stroke=None, extra="") -> str:
    line = f' stroke="{stroke}"' if stroke else ""
    corner = f' rx="{num(rx)}"' if rx else ""
    return f'<rect x="{num(x)}" y="{num(y)}" width="{num(w)}" height="{num(h)}"{corner} fill="{fill}"{line}{extra}/>'


def chip(x, y, label, t, *, on=False, size=12, h=24, fill=None, color=None) -> tuple[float, str]:
    """둥근 칩. `on`이면 강조색 바탕. (폭, SVG)를 돌려준다."""
    w = text_width(label, size) + 20
    bg = fill or (t["accent"] if on else t["surface2"])
    fg = color or (t["on_accent"] if on else t["t2"])
    return w, rect(x, y, w, h, rx=h / 2, fill=bg) + text(x + w / 2, y + h / 2 + size * 0.36, label, size=size,
                                                        weight=700, fill=fg, anchor="middle")


def button(x, y, label, t, *, variant="outlined", h=26, size=12, w=None) -> tuple[float, str]:
    """Viewer 단추. `variant`: filled(강조 바탕) · tonal(표면 2) · outlined. (폭, SVG)."""
    w = w or text_width(label, size) + 22
    ty = y + h / 2 + size * 0.36
    if variant == "filled":
        body = rect(x, y, w, h, rx=6, fill=t["accent"]) + text(x + w / 2, ty, label, size=size, weight=700,
                                                             fill=t["on_accent"], anchor="middle")
    elif variant == "tonal":
        body = rect(x, y, w, h, rx=6, fill=t["surface2"], stroke=t["line"]) + text(
            x + w / 2, ty, label, size=size, weight=600, fill=t["t1"], anchor="middle")
    else:
        body = rect(x, y, w, h, rx=6, fill=t["surface"], stroke=t["line"]) + text(
            x + w / 2, ty, label, size=size, weight=600, fill=t["t1"], anchor="middle")
    return w, body


def tap(x, y, t, style) -> str:
    """누르는 곳을 알리는 동그라미."""
    return (f'<circle class="tap" style="{style}" cx="{num(x)}" cy="{num(y)}" r="15" fill="{t["t1"]}" '
            f'fill-opacity=".2" stroke="{t["t1"]}" stroke-opacity=".5" stroke-width="2"/>')


def lines(x, y, widths, t, *, gap=14, h=6, fill=None, style="") -> str:
    """본문 글줄을 회색 막대로 줄여 그린다."""
    cls = f' class="rise" style="{style}"' if style else ""
    bars = "".join(rect(x, y + i * gap, w, h, rx=h / 2, fill=fill or t["line_strong"]) for i, w in enumerate(widths))
    return f"<g{cls}>{bars}</g>"


def motion_css(*, scene_length: float, scenes: int, font: str = FONT) -> str:
    """애니메이션 CSS. 장면 그룹(`.sc`)이 자기 차례에만 보이고, 한 바퀴에 움직임이 한 번씩 일어난다."""
    cycle = scene_length * scenes

    def p(s: float) -> str:
        return f"{s / cycle * 100:.3f}%"

    # 장면 안 요소는 자기 장면이 끝난 뒤(요소 기준 scene_length초) 숨는다. 그래야 다음 바퀴에 장면이 다시
    # 보일 때 늦게 나올 요소가 앞 바퀴의 마지막 모습으로 먼저 보였다가 사라지지 않는다.
    end, gone = p(scene_length), p(scene_length + 0.01)
    return f"""
text{{font-family:{font}}}
.sc{{opacity:0;animation:scene {num(cycle)}s linear infinite backwards}}
.rise{{transform-box:fill-box;animation:rise {num(cycle)}s cubic-bezier(.2,.8,.2,1) infinite backwards}}
.pop{{transform-box:fill-box;transform-origin:center;animation:pop {num(cycle)}s ease-out infinite backwards}}
.fade{{opacity:0;animation:fade {num(cycle)}s linear infinite backwards}}
.flow{{stroke-dasharray:6 6;animation:flow {num(cycle)}s linear infinite backwards}}
.tap{{transform-box:fill-box;transform-origin:center;opacity:0;animation:tap {num(cycle)}s ease-out infinite backwards}}
@keyframes scene{{0%{{opacity:0}}{p(0.35)}{{opacity:1}}{p(scene_length - 0.35)}{{opacity:1}}{p(scene_length)}{{opacity:0}}100%{{opacity:0}}}}
@keyframes rise{{0%{{opacity:0;transform:translateY(12px)}}{p(0.45)},{end}{{opacity:1;transform:none}}{gone},100%{{opacity:0;transform:none}}}}
@keyframes pop{{0%{{transform:scale(0)}}{p(0.3)}{{transform:scale(1.12)}}{p(0.45)},{end}{{transform:scale(1)}}{gone},100%{{transform:scale(0)}}}}
@keyframes fade{{0%{{opacity:0}}{p(0.3)},{end}{{opacity:1}}{gone},100%{{opacity:0}}}}
@keyframes flow{{0%{{stroke-dashoffset:0}}100%{{stroke-dashoffset:{-12 * round(cycle * 2)}}}}}
@keyframes tap{{0%{{opacity:0;transform:scale(.4)}}{p(0.12)}{{opacity:1;transform:scale(1)}}{p(0.7)}{{opacity:0;transform:scale(1.9)}}100%{{opacity:0;transform:scale(1.9)}}}}
@media (prefers-reduced-motion:reduce){{
*{{animation:none!important}}
.s0{{opacity:1}}
.rise,.fade{{opacity:1;transform:none}}
.pop{{transform:none}}
.tap{{opacity:0}}
}}"""


def svg_document(*, w: int, h: int, title: str, desc: str, css: str, body: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" '
            f'aria-labelledby="motion-title motion-desc">\n'
            f'<title id="motion-title">{esc(title)}</title>\n'
            f'<desc id="motion-desc">{esc(desc)}</desc>\n'
            f"<style>{css}</style>\n{body}\n</svg>\n")

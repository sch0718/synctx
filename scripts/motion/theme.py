"""그림 색을 Viewer 테마 토큰(services/viewer/src/renderer.py의 THEME_VARS_CSS)에서 읽는다."""
from __future__ import annotations

import re
from pathlib import Path

# 회의 모드 형광펜·메모 종류 색(services/viewer/src/static/meeting/meeting.css와 같은 값).
MEETING = {"hl": "rgba(255,212,0,0.35)", "hl_line": "#9a6700", "decision": "#0b5cad", "action": "#8a4600"}


def _hex(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def mix(a: str, b: str, ratio: float) -> str:
    """색 `a`를 `ratio`만큼, 나머지는 `b`로 섞는다."""
    return "#" + "".join(f"{round(x * ratio + y * (1 - ratio)):02x}" for x, y in zip(_hex(a), _hex(b)))


def _block(css: str, name: str) -> dict[str, str]:
    match = re.search(r'\[data-theme="' + re.escape(name) + r'"\]\s*\{(.*?)\}', css, re.S)
    if not match:
        raise ValueError(f"THEME_VARS_CSS에 {name} 테마가 없습니다")
    return dict(re.findall(r"--([\w-]+):\s*([^;]+);", match.group(1)))


def load_theme(renderer_py: Path) -> dict[str, dict[str, str]]:
    source = renderer_py.read_text(encoding="utf-8")
    css = re.search(r'THEME_VARS_CSS = """(.*?)"""', source, re.S).group(1)

    def pick(v: dict[str, str], dark: bool) -> dict[str, str]:
        bg, primary = v["bg-default"], v["primary"]
        return {
            "bg": bg,
            "surface": v["bg-card"],
            "surface2": v["bg-neutral"],
            "line": v["border-color"],
            # 글줄 막대는 테두리보다 진해야 보인다.
            "line_strong": mix(v["text-secondary"], bg, 0.28),
            "t1": v["text-primary"],
            "t2": v["text-secondary"],
            "t3": v["text-disabled"],
            "accent": primary,
            # github-dark의 --primary-lighter는 배경과 같아 강조 바탕으로 쓸 수 없다.
            "accent_soft": mix(primary, bg, 0.18) if dark else v["primary-lighter"],
            "on_accent": v["on-primary"],
            "window": mix(v["text-secondary"], bg, 0.12),
            **MEETING,
        }

    return {"light": pick(_block(css, "github-light"), False), "dark": pick(_block(css, "github-dark"), True)}

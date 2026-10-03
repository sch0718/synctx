"""WCAG 2.1 대비 계산 테스트 헬퍼."""

import re


def rgb(value):
    value = value.strip()
    m = re.fullmatch(r"rgba?\(([^)]*)\)", value)
    if m:
        parts = [p.strip() for p in m.group(1).split(",")]
        return tuple(int(p) for p in parts[:3]), float(parts[3]) if len(parts) == 4 else 1.0
    hex_ = value.lstrip("#")
    if len(hex_) == 3:
        hex_ = "".join(c * 2 for c in hex_)
    return tuple(int(hex_[i:i + 2], 16) for i in (0, 2, 4)), 1.0


def luminance(color):
    def channel(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(v) for v in color)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def theme_vars(theme):
    from renderer import THEME_VARS_CSS
    block = re.search(rf'\[data-theme="{theme}"\] \{{([^}}]*)\}}', THEME_VARS_CSS).group(1)
    return dict(re.findall(r"(--[\w-]+):\s*([^;]+);", block))

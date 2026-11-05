"""테마 CSS 외부 파일 출력과 정적 모드가 쓰는 테마 표."""

from __future__ import annotations

from pathlib import Path

from renderer import THEME_CSS, THEME_HLJS, THEME_MERMAID

THEME_DIR = "view/static/themes"
HLJS_DIR = "view/static/vendor/hljs"
DEFAULT_THEME = "corporate"

THEME_TABLE: dict[str, dict[str, str]] = {
    name: {"css": f"{THEME_DIR}/{name}.css", "hljs": THEME_HLJS.get(name, "github"), "mermaid": THEME_MERMAID.get(name, "default")}
    for name in THEME_CSS
}


def export_theme_assets(out_dir: Path) -> dict[str, str]:
    target = Path(out_dir) / THEME_DIR
    target.mkdir(parents=True, exist_ok=True)
    written = {}
    for name, css in THEME_CSS.items():
        (target / f"{name}.css").write_text(css, encoding="utf-8")
        written[name] = THEME_TABLE[name]["css"]
    return written


def hljs_url(name: str) -> str:
    return f"/{HLJS_DIR}/{THEME_TABLE[name]['hljs']}.min.css"


def css_url(name: str) -> str:
    return "/" + THEME_TABLE[name]["css"]


def browser_table() -> dict[str, dict[str, str]]:
    return {name: {"css": css_url(name), "hljs": hljs_url(name), "mermaid": entry["mermaid"]} for name, entry in THEME_TABLE.items()}

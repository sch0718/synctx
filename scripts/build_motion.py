#!/usr/bin/env python3
"""README 소개 그림과 메뉴별 도움말(그림·글)을 같은 단계 정의로 만든다.

- docs/readme/hero-{light,dark}.svg: README 맨 위 소개 그림
- docs/help/img/<id>-{light,dark}.svg, docs/help/<id>.md, docs/help/README.md: 메뉴별 도움말

색은 Viewer 테마 토큰(services/viewer/src/renderer.py)에서 읽는다. 화면 문구가 바뀌면
scripts/motion/help_pages.py를 고치고 `python3 scripts/build_motion.py`로 다시 만든다.
scripts/tests/test_motion.py가 커밋된 파일이 생성 결과와 같은지 확인한다.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from motion import help_pages, hero, kit  # noqa: E402,F401  (테스트가 모듈을 함께 쓴다)
from motion.help import render_help, render_help_index, render_help_markdown  # noqa: E402
from motion.hero import render_hero  # noqa: E402
from motion.theme import load_theme  # noqa: E402


def outputs(root: Path) -> dict[str, str]:
    """만들 파일(저장소 기준 경로 → 내용)."""
    theme = load_theme(root / "services/viewer/src/renderer.py")
    files: dict[str, str] = {}
    for mode in ("light", "dark"):
        files[f"docs/readme/hero-{mode}.svg"] = render_hero(theme[mode])
        for page in help_pages.PAGES:
            files[f"docs/help/img/{page.id}-{mode}.svg"] = render_help(page, theme[mode], mode)
    for page in help_pages.PAGES:
        files[f"docs/help/{page.id}.md"] = render_help_markdown(page)
    files["docs/help/README.md"] = render_help_index(help_pages.PAGES)
    return files


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    for path, content in outputs(root).items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        print(f"{path}: {len(content.encode())} bytes")


if __name__ == "__main__":
    main()

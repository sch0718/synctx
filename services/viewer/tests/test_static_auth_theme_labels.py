"""인증 화면의 테마 이름표는 Viewer와 같은 순서와 문구를 쓴다."""

import re
from pathlib import Path

from viewer_ui import THEME_LABELS


def test_auth_theme_labels_match_viewer_in_order():
    layout = Path(__file__).resolve().parents[3] / "deploy/pages/src/pages/layout.ts"
    source = layout.read_text(encoding="utf-8")
    block = re.search(r"export const THEME_LABELS\s*=\s*\{(.*?)\}", source, re.DOTALL)
    assert block is not None, "인증 화면에 THEME_LABELS 객체가 있어야 한다"
    labels = re.findall(r"'([a-z-]+)':\s*'([^']+)'", block.group(1))
    assert labels == list(THEME_LABELS.items())

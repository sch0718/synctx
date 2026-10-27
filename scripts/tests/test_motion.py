"""README·도움말 그림과 글은 생성기 결과와 같고, GitHub에서 그대로 보이는 SVG다."""
import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HELP_IDS = ("browse", "search", "document", "meeting", "present", "evidence", "slides")


def build():
    spec = importlib.util.spec_from_file_location("build_motion", ROOT / "scripts/build_motion.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generated_files_are_current():
    outputs = build().outputs(ROOT)
    assert len([path for path in outputs if path.endswith(".svg")]) == 2 + len(HELP_IDS) * 2
    stale = [path for path, text in outputs.items()
             if not (ROOT / path).is_file() or (ROOT / path).read_text(encoding="utf-8") != text]
    assert stale == [], "python3 scripts/build_motion.py 로 다시 만드세요"


def test_outputs_are_deterministic():
    assert build().outputs(ROOT) == build().outputs(ROOT)


def test_every_help_page_has_four_steps():
    pages = build().help_pages.PAGES
    assert tuple(page.id for page in pages) == HELP_IDS
    assert all(len(page.steps) == 4 for page in pages)


@pytest.mark.parametrize("path", sorted(f"docs/help/img/{i}-{m}.svg" for i in HELP_IDS for m in ("light", "dark"))
                         + ["docs/readme/hero-light.svg", "docs/readme/hero-dark.svg"])
def test_svgs_are_self_contained_and_small(path):
    svg = (ROOT / path).read_text(encoding="utf-8")
    # GitHub는 SVG를 <img>로 보여 주므로 스크립트·외부 글꼴·외부 자원이 없어야 한다.
    assert len(svg.encode()) <= 40_000
    assert "<script" not in svg and "@import" not in svg and "@font-face" not in svg
    assert re.findall(r"https?://[^\s\"'<>]+", svg) == ["http://www.w3.org/2000/svg"]
    assert "prefers-reduced-motion" in svg


def test_scene_children_hide_before_their_scene_repeats():
    # 장면 안 요소가 바퀴 끝에 숨지 않으면 다음 바퀴에 늦게 나올 요소가 먼저 보인다.
    css = build().kit.motion_css(scene_length=3.8, scenes=4)
    for name in ("rise", "pop", "fade"):
        frames = re.search(r"@keyframes " + name + r"\{(.*?)\}\}", css).group(1)
        last = frames.rsplit("100%{", 1)[1]
        assert "opacity:0" in last or "scale(0)" in last, name


def test_hero_meeting_example_is_valid_front_matter():
    import yaml
    from synctx_common.frontmatter import validate_meeting

    lines = build().hero.MEETING_EXAMPLE
    meeting, errors = validate_meeting(yaml.safe_load("\n".join(lines[1:lines.index("---", 1)])))
    assert errors == [] and meeting is not None


DOCS = ["README.md", "docs/development.md", "docs/help/README.md", *[f"docs/help/{i}.md" for i in HELP_IDS]]


@pytest.mark.parametrize("doc", DOCS)
def test_relative_links_resolve(doc):
    text = (ROOT / doc).read_text(encoding="utf-8")
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    targets = re.findall(r"\]\(([^)\s]+)\)", text) + re.findall(r'(?:src|srcset)="([^"]+)"', text)
    missing = [target for target in targets
               if not re.match(r"[a-z]+:", target) and not target.startswith("#")
               and not ((ROOT / doc).parent / target.split("#", 1)[0]).exists()]
    assert missing == []

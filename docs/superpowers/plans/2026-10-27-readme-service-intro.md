# README Service Intro Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite README as a service introduction with an animated hero, move developer content to `docs/development.md`, and add seven generated Viewer help pages with animated SVGs.

**Architecture:** A stdlib-only Python generator (`scripts/motion/`) turns step definitions into hero/help SVGs (light/dark) and help Markdown, reading colors from the Viewer theme tokens. A pytest keeps generated files current and checks SVG constraints and relative links.

**Tech Stack:** Python 3.13 stdlib, pytest (via `uv run --frozen --all-packages`), Markdown, SVG + CSS animation.

## Global Constraints

- Branch `docs/readme-service-intro` from `origin/main`; PR #61 README changes are out of scope.
- SVGs: no `<script`, no `@import`, no external URLs except the SVG xmlns, no web fonts; ≤ 40KB each; `prefers-reduced-motion` stops on the first scene.
- Screen copy uses the Viewer's real labels (`viewer_ui.py`, `app.py`, `static/*.js`).
- Generated files carry a header comment naming `python3 scripts/build_motion.py` and `scripts/motion/help_pages.py`; output is byte-for-byte deterministic.
- CLAUDE.md consistency rules move from README to `docs/development.md`; README Mermaid subgraph rule applies there.
- Korean prose, existing document tone (평서형 `-다` in docs/development.md; help pages and README intro may use the friendly `-요`/`-니다` style used by the two reference repositories — use `-니다`).

---

### Task 1: Motion generator and help pages

**Files:**
- Create: `scripts/motion/__init__.py`, `scripts/motion/kit.py`, `scripts/motion/theme.py`, `scripts/motion/hero.py`, `scripts/motion/help.py`, `scripts/motion/help_pages.py`, `scripts/build_motion.py`
- Create (generated): `docs/readme/hero-{light,dark}.svg`, `docs/help/img/<id>-{light,dark}.svg`, `docs/help/<id>.md`, `docs/help/README.md` for ids `browse search document meeting present evidence slides`
- Test: `scripts/tests/test_motion.py`

**Interfaces:**
- Produces: `build_motion.outputs(root: Path) -> dict[str, str]` (relative path → file text); `build_motion.main()` writes them.
- `theme.load_theme(renderer_py: Path) -> dict[str, dict[str, str]]` with keys `light`, `dark`; each has `bg surface surface2 line t1 t2 t3 accent accent_soft on_accent hl hl_line decision action`.
- `help_pages.PAGES: list[Page]` where `Page` has `id, title, lead: list[str], steps: list[Step] (4), tips: list[str]`; `Step` has `title, sub, screen: Callable[[Ctx], str]`.

- [ ] **Step 1: Write the failing test** — `scripts/tests/test_motion.py`:

```python
"""README·도움말 그림과 글은 생성기 결과와 같고, GitHub에서 그대로 보이는 SVG다."""
import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def build():
    spec = importlib.util.spec_from_file_location("build_motion", ROOT / "scripts/build_motion.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generated_files_are_current():
    outputs = build().outputs(ROOT)
    assert len([p for p in outputs if p.endswith(".svg")]) == 2 + 7 * 2
    stale = [path for path, text in outputs.items() if not (ROOT / path).is_file() or (ROOT / path).read_text() != text]
    assert stale == [], "python3 scripts/build_motion.py 로 다시 만드세요"


def test_outputs_are_deterministic():
    assert build().outputs(ROOT) == build().outputs(ROOT)


@pytest.mark.parametrize("path", sorted(str(p.relative_to(ROOT)) for p in (ROOT / "docs").rglob("*.svg") if "readme" in p.parts or "help" in p.parts))
def test_svgs_are_self_contained_and_small(path):
    svg = (ROOT / path).read_text()
    assert len(svg.encode()) <= 30_000
    assert "<script" not in svg and "@import" not in svg and "@font-face" not in svg
    assert re.findall(r"https?://[^\s\"']+", svg) == ["http://www.w3.org/2000/svg"]
    assert "prefers-reduced-motion" in svg


def test_every_help_page_has_four_steps():
    pages = build().help_pages.PAGES
    assert [p.id for p in pages] == ["browse", "search", "document", "meeting", "present", "evidence", "slides"]
    assert all(len(p.steps) == 4 for p in pages)


DOCS = ["README.md", "docs/development.md", "docs/help/README.md", *[f"docs/help/{i}.md" for i in
        ("browse", "search", "document", "meeting", "present", "evidence", "slides")]]


@pytest.mark.parametrize("doc", DOCS)
def test_relative_links_resolve(doc):
    text = (ROOT / doc).read_text()
    targets = re.findall(r"\]\(([^)\s]+)\)", text) + re.findall(r'(?:src|srcset)="([^"]+)"', text)
    missing = []
    for target in targets:
        if re.match(r"[a-z]+:", target) or target.startswith("#"):
            continue
        path = (ROOT / doc).parent / target.split("#", 1)[0]
        if not path.exists():
            missing.append(target)
    assert missing == []
```

- [ ] **Step 2: Run to verify it fails** — `uv run --frozen --all-packages pytest scripts/tests/test_motion.py -q` → FAIL (`build_motion.py` missing).
- [ ] **Step 3: Implement** `kit.py` (text/text_width/fit/chip/card/button/tap/motion_css/svg_document, port of gazua `kit.mjs`), `theme.py`, `hero.py` (4 scenes, 960×440), `help.py` (960×520, steps left, browser window right; `render_help`, `render_help_markdown`, `render_help_index`), `help_pages.py` (7 pages × 4 steps with real labels), `build_motion.py` (`outputs`, `main`).
- [ ] **Step 4: Generate and inspect** — `python3 scripts/build_motion.py`; open each SVG light/dark in the browser and fix overlaps/cut-offs.
- [ ] **Step 5: Run test** — link test for README/development fails until Task 2; the other tests pass.
- [ ] **Step 6: Commit** `docs(help): generate Viewer help pages and README hero from one step definition`.

### Task 2: README rewrite, docs/development.md, CLAUDE.md rules

**Files:**
- Modify: `README.md` (service intro), `CLAUDE.md` (document table, consistency rules, Mermaid rule)
- Create: `docs/development.md` (old README body)
- Modify: any document that links `README.md#<section>` for moved sections (find with `grep -rn "README.md#" docs services scripts helm`).
- Test: `scripts/tests/test_motion.py::test_relative_links_resolve`

- [ ] **Step 1:** Move the old README body (from `## 프로젝트 배경` to `## 문서`, except `## 버전별 주요 변경`) to `docs/development.md` with a title and a short note that README is the service intro; rewrite `docs/…` links relative to `docs/`, `README.md` → `../README.md`, root paths → `../`.
- [ ] **Step 2:** Write README: hero `<picture>`, tagline, intro, feature table, 시작하기, 간단한 사용법, 메뉴별 도움말 table, 데이터는 어디에 있나요, 릴리스 (old version table), 개발자용 links.
- [ ] **Step 3:** CLAUDE.md: README row → service intro; add `docs/development.md` row; consistency rules and Mermaid rule point at `docs/development.md`.
- [ ] **Step 4:** Fix `README.md#…` references found by grep.
- [ ] **Step 5:** `uv run --frozen --all-packages pytest scripts/tests/test_motion.py -q` → PASS; `scripts/test-all.sh` → ALL TESTS PASSED; `git diff --check`.
- [ ] **Step 6: Commit** `docs: README를 서비스 소개 중심으로, 개발 문서를 docs/development.md로 분리`.

### Task 3: PR, Codex review, merge

- [ ] **Step 1:** Push the branch and open a PR against `main`.
- [ ] **Step 2:** Codex review of the branch (`codex-review` `run-review.sh --base origin/main`, one run at a time).
- [ ] **Step 3:** Fix actionable findings, re-run tests, push; re-run Codex review on the fixes if code changed materially.
- [ ] **Step 4:** Merge the PR (user-approved after Codex review) and confirm `main` contains it.

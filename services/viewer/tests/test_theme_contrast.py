"""Viewer 공용 테마 변수의 글자 대비 테스트 (WCAG 2.1 AA 4.5:1)."""

import re
from pathlib import Path

import pytest

from contrast import contrast, rgb, theme_vars


def _themes():
    from renderer import THEME_CSS
    return list(THEME_CSS)


def _app_source():
    import app
    return Path(app.__file__).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ["--text-primary", "--text-secondary", "--primary"])
def test_text_colors_meet_aa_on_theme_backgrounds(name):
    failures = []
    for theme in _themes():
        values = theme_vars(theme)
        for bg in ("--bg-default", "--bg-neutral", "--bg-card"):
            ratio = contrast(rgb(values[name])[0], rgb(values[bg])[0])
            if ratio < 4.5:
                failures.append(f"{theme} {bg}: {ratio:.2f}")
    assert not failures, failures


@pytest.mark.parametrize("bg", ["--primary", "--primary-dark"])
def test_button_text_meets_aa_on_primary_backgrounds(bg):
    failures = []
    for theme in _themes():
        values = theme_vars(theme)
        ratio = contrast(rgb(values["--on-primary"])[0], rgb(values[bg])[0])
        if ratio < 4.5:
            failures.append(f"{theme}: {ratio:.2f}")
    assert not failures, failures


def _css_source():
    return (Path(__file__).resolve().parents[1] / "src" / "static" / "viewer.css").read_text(encoding="utf-8")


def _css_rule(selector, css=None):
    return re.search(rf"(?m)^{re.escape(selector)} \{{([^}}]*)\}}", _css_source() if css is None else css).group(1)


def test_primary_buttons_use_on_primary_text():
    assert re.search(r"(?<![-\w])color:\s*var\(--on-primary\)", _css_rule(".btn-primary"))


def _decl(rule, prop):
    m = re.search(rf"(?<![-\w]){prop}:\s*([^;]+);", rule)
    return m.group(1).strip() if m else None


def _resolve(value, values):
    ref = re.fullmatch(r"var\((--[\w-]+)(?:,\s*(.+))?\)", value)
    if not ref:
        return value
    if ref.group(2) is None or ref.group(1) in values:
        return values[ref.group(1)]
    return _resolve(ref.group(2).strip(), values)


@pytest.mark.parametrize("state", ["", ":hover"])
def test_secondary_button_text_meets_aa(state):
    base = _css_rule(".btn")
    background = _decl(_css_rule(f".btn{state}"), "background") if state else _decl(base, "background")
    color = _decl(base, "color")
    failures = []
    for theme in _themes():
        values = theme_vars(theme)
        ratio = contrast(rgb(_resolve(color, values))[0], rgb(_resolve(background, values))[0])
        if ratio < 4.5:
            failures.append(f"{theme}: {ratio:.2f}")
    assert not failures, failures


def test_text_disabled_is_only_used_for_icons():
    lines = [line.strip() for src in (_app_source(), _css_source()) for line in src.splitlines()
             if re.search(r"(?<![-\w])color:\s*var\(--text-disabled\)", line)]
    assert lines
    assert all(re.match(r"\.(icon-|\S+ svg)", line) for line in lines), lines


def test_nav_tab_labels_meet_aa_on_their_backgrounds():
    track = _decl(_css_rule(".nav-tabs"), "background")
    unselected = _decl(_css_rule('.nav-tabs [role="tab"]'), "color")
    selected_rule = _css_rule('.nav-tabs [role="tab"][aria-selected="true"]')
    cases = [("unselected", unselected, track), ("selected", _decl(selected_rule, "color"), _decl(selected_rule, "background"))]
    failures = []
    for theme in _themes():
        values = theme_vars(theme)
        for label, color, background in cases:
            ratio = contrast(rgb(_resolve(color, values))[0], rgb(_resolve(background, values))[0])
            if ratio < 4.5:
                failures.append(f"{theme} {label}: {ratio:.2f}")
    assert not failures, failures


MARK_SELECTOR = ".search-page mark, .finder-results mark"


def test_mark_rule_is_scoped_to_search_results():
    assert not re.search(r"(?m)^mark\s*\{", _css_source())
    assert _css_rule(MARK_SELECTOR)


def test_mark_text_meets_aa_on_its_background():
    rule = _css_rule(MARK_SELECTOR)
    color, background = _decl(rule, "color"), _decl(rule, "background")
    failures = []
    for theme in _themes():
        values = theme_vars(theme)
        ratio = contrast(rgb(_resolve(color, values))[0], rgb(_resolve(background, values))[0])
        if ratio < 4.5:
            failures.append(f"{theme}: {ratio:.2f}")
    assert not failures, failures


def test_mark_is_visible_without_relying_on_background_alone():
    rule = _css_rule(MARK_SELECTOR)
    assert _decl(rule, "font-weight") and "var(--primary)" in (_decl(rule, "box-shadow") or "")


def test_meeting_exit_link_meets_aa():
    color = _decl(_css_rule(".meeting-exit"), "color")
    assert color
    failures = []
    for theme in _themes():
        values = theme_vars(theme)
        ratio = contrast(rgb(_resolve(color, values))[0], rgb(values["--bg-default"])[0])
        if ratio < 4.5:
            failures.append(f"{theme}: {ratio:.2f}")
    assert not failures, failures


@pytest.mark.parametrize("theme", _themes())
@pytest.mark.parametrize("selector", [".present-top", ".present-nav"])
def test_present_bands_meet_aa(theme, selector):
    rule = _css_rule(selector)
    values = theme_vars(theme)
    color, background = _resolve(_decl(rule, "color"), values), _resolve(_decl(rule, "background"), values)
    assert contrast(rgb(color)[0], rgb(background)[0]) >= 4.5


@pytest.mark.parametrize("theme", _themes())
@pytest.mark.parametrize("selector", [".meeting-guide", ".meeting-guide-title"])
def test_meeting_guide_text_meets_aa(theme, selector):
    values = theme_vars(theme)
    color = _resolve(_decl(_css_rule(selector), "color"), values)
    background = _resolve(_decl(_css_rule(".meeting-guide"), "background"), values)
    assert contrast(rgb(color)[0], rgb(background)[0]) >= 4.5


@pytest.mark.parametrize("theme", _themes())
def test_meeting_guide_open_button_meets_aa(theme):
    values = theme_vars(theme)
    color = _resolve(_decl(_css_rule(".meeting-guide-open"), "color"), values)
    assert contrast(rgb(color)[0], rgb(values["--bg-default"])[0]) >= 4.5


def _evidence_css():
    return (Path(__file__).resolve().parents[1] / "src" / "static" / "meeting" / "evidence.css").read_text(encoding="utf-8")


EVIDENCE_PAIRS = [
    (".evidence-pop", ".evidence-pop"),
    (".evidence-pop-loc", ".evidence-pop"),
    (".evidence-pop-hint", ".evidence-pop"),
    (".evidence-list", ".evidence-list"),
    (".evidence-count", ".evidence-list"),
    (".evidence-entry", ".evidence-entry"),
    (".evidence-loc", ".evidence-entry"),
    (".evidence-meta", ".evidence-entry"),
    (".evidence-meta a", ".evidence-entry"),
    (".evidence-entry", ".evidence-entry:focus, .evidence-entry:target"),
]


# None은 테마 변수가 없는 오프라인 사본(var() 기본값만 쓰인다)
@pytest.mark.parametrize("theme", _themes() + [None])
@pytest.mark.parametrize("text,background", EVIDENCE_PAIRS)
def test_evidence_text_meets_aa(theme, text, background):
    css = _evidence_css()
    values = theme_vars(theme) if theme else {}
    color = _resolve(_decl(_css_rule(text, css), "color"), values)
    back = _resolve(_decl(_css_rule(background, css), "background"), values)
    assert contrast(rgb(color)[0], rgb(back)[0]) >= 4.5


def test_jitsi_panel_reasons_and_disabled_button_keep_aa_contrast():
    css = (Path(__file__).resolve().parents[1] / 'src/static/meeting/jitsi.css').read_text()
    for selector in ('.jitsi-join:disabled', '.jitsi-panel'):
        rule = _css_rule(selector, css)
        assert _decl(rule, 'color') == 'var(--text-primary)'
        background = '--bg-neutral' if selector == '.jitsi-join:disabled' else '--bg-card'
        assert _decl(rule, 'background') == f'var({background})'
        for theme in _themes():
            values = theme_vars(theme)
            assert contrast(rgb(values['--text-primary'])[0], rgb(values[background])[0]) >= 4.5
    assert _decl(_css_rule('.jitsi-join:disabled', css), 'opacity') == '1'
    assert 'outline: 2px solid var(--primary)' in css

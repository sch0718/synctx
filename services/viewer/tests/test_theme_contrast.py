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


def _css_rule(selector):
    return re.search(rf"(?m)^{re.escape(selector)} \{{([^}}]*)\}}", _css_source()).group(1)


def test_primary_buttons_use_on_primary_text():
    assert re.search(r"(?<![-\w])color:\s*var\(--on-primary\)", _css_rule(".btn-primary"))


def _decl(rule, prop):
    m = re.search(rf"(?<![-\w]){prop}:\s*([^;]+);", rule)
    return m.group(1).strip() if m else None


def _resolve(value, values):
    ref = re.fullmatch(r"var\((--[\w-]+)\)", value)
    return values[ref.group(1)] if ref else value


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

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


def test_primary_buttons_use_on_primary_text():
    rule = re.search(r"\.viewer-toolbar \.btn \{\{([^}]*)\}\}", _app_source()).group(1)
    assert re.search(r"(?<![-\w])color:\s*var\(--on-primary\)", rule)


def _toolbar_rule(selector):
    pattern = rf"\.viewer-toolbar {re.escape(selector)} \{{\{{([^}}]*)\}}\}}"
    return re.search(pattern, _app_source()).group(1)


def _decl(rule, prop):
    m = re.search(rf"(?<![-\w]){prop}:\s*([^;]+);", rule)
    return m.group(1).strip() if m else None


def _resolve(value, values):
    ref = re.fullmatch(r"var\((--[\w-]+)\)", value)
    return values[ref.group(1)] if ref else value


@pytest.mark.parametrize("state", ["", ":hover"])
def test_secondary_button_text_meets_aa(state):
    base = _toolbar_rule(".btn-secondary")
    background = _decl(_toolbar_rule(f".btn-secondary{state}"), "background") if state else _decl(base, "background")
    color = _decl(base, "color") or _decl(_toolbar_rule(".btn"), "color")
    failures = []
    for theme in _themes():
        values = theme_vars(theme)
        ratio = contrast(rgb(_resolve(color, values))[0], rgb(_resolve(background, values))[0])
        if ratio < 4.5:
            failures.append(f"{theme}: {ratio:.2f}")
    assert not failures, failures


def test_text_disabled_is_only_used_for_icons():
    lines = [line.strip() for line in _app_source().splitlines()
             if re.search(r"(?<![-\w])color:\s*var\(--text-disabled\)", line)]
    assert lines
    assert all(re.match(r"\.(icon-|\S+ svg)", line) for line in lines), lines

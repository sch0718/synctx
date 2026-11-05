from pathlib import Path

import pytest

from static_export import verify
from static_export.verify import MAX_FILES, MAX_FILE_BYTES, verify_dist

FAKE_TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"


def _write(root: Path, rel: str, text: str = "") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _page(body: str) -> str:
    return f"<!doctype html><html><head><title>t</title></head><body>{body}</body></html>"


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    root = tmp_path / "dist"
    _write(root, "_headers", "/*\n  X-Robots-Tag: noindex\n")
    _write(root, "_redirects", "/ /view/ 302\n")
    _write(root, "robots.txt", "User-agent: *\nDisallow: /\n")
    _write(root, "view/index.html", _page('<a href="/view/docs/">docs</a>'))
    _write(root, "view/docs/index.html", _page(
        '<link href="/view/static/app.css" rel="stylesheet">'
        '<a href="guide/">guide</a><a href="#top">top</a>'
        '<a href="https://example.com/x">ext</a>'
        '<img src="/view/docs/raw/img/a%20b.png" srcset="/view/docs/raw/img/a%20b.png 1x, /view/docs/raw/img/a%20b.png 2x">'
    ))
    _write(root, "view/docs/guide/index.html", _page('<a href="../">up</a>'))
    _write(root, "view/static/app.css", "body{}")
    _write(root, "view/docs/raw/img/a b.png", "png")
    return root


def test_verify_passes_clean_dist(dist):
    assert verify_dist(dist, secret_values=[FAKE_TOKEN]) == []


def test_verify_flags_secret_value(dist):
    _write(dist, "view/docs/leak.txt", f"token={FAKE_TOKEN}")
    violations = verify_dist(dist, secret_values=[FAKE_TOKEN])
    assert any("leak.txt" in v for v in violations)


def test_verify_flags_secret_value_in_path(dist):
    _write(dist, f"view/{FAKE_TOKEN}.txt", "x")
    violations = verify_dist(dist, secret_values=[FAKE_TOKEN])
    assert violations
    assert not any(FAKE_TOKEN in v for v in violations)


def test_verify_ignores_empty_secret_values(dist):
    assert verify_dist(dist, secret_values=["", "  "]) == []


@pytest.mark.parametrize("leak", [
    "ghp_" + "Z9y8X7w6V5u4T3s2R1q0",
    "github_pat_" + "11ABCDEFG0abcdefgh_1234567890",
    "Authorization: Bearer abc.def.ghi",
])
def test_verify_flags_token_patterns(dist, leak):
    _write(dist, "view/docs/note.html", _page(leak))
    violations = verify_dist(dist, secret_values=[])
    assert any("note.html" in v for v in violations)
    assert not any(leak in v for v in violations)


def test_verify_allows_plain_word_authorization(dist):
    _write(dist, "view/docs/note.html", _page("Authorization 헤더 설명"))
    assert verify_dist(dist, secret_values=[]) == []


def test_verify_flags_git_directory(dist):
    _write(dist, "view/docs/.git/config", "x")
    assert any(".git" in v for v in verify_dist(dist, secret_values=[]))


def test_verify_flags_too_many_files(dist, monkeypatch):
    monkeypatch.setattr(verify, "MAX_FILES", 3)
    assert any("파일 수" in v for v in verify_dist(dist, secret_values=[]))
    assert MAX_FILES == 20_000


def test_verify_flags_oversized_file(dist, monkeypatch):
    monkeypatch.setattr(verify, "MAX_FILE_BYTES", 5)
    violations = verify_dist(dist, secret_values=[])
    assert any("파일 크기" in v for v in violations)
    assert MAX_FILE_BYTES == 25 * 1024 * 1024


def test_verify_flags_missing_required_files(dist):
    (dist / "_headers").unlink()
    (dist / "robots.txt").unlink()
    violations = verify_dist(dist, secret_values=[])
    assert any("_headers" in v for v in violations)
    assert any("robots.txt" in v for v in violations)


def test_verify_flags_missing_dist(tmp_path):
    assert verify_dist(tmp_path / "none", secret_values=[])


@pytest.mark.parametrize("href", [
    "/view/docs/missing/",
    "missing.png",
    "../../../../etc/passwd",
    "/view/docs/raw/img/none.png",
])
def test_verify_flags_broken_links(dist, href):
    _write(dist, "view/docs/broken/index.html", _page(f'<a href="{href}">x</a>'))
    violations = verify_dist(dist, secret_values=[])
    assert any("broken/index.html" in v for v in violations)


def test_verify_flags_broken_src_and_srcset(dist):
    _write(dist, "view/docs/broken/index.html", _page(
        '<img src="/view/none.png"><img srcset="/view/ok.png 1x, /view/none2.png 2x">'))
    violations = verify_dist(dist, secret_values=[])
    assert len([v for v in violations if "broken/index.html" in v]) >= 2


def test_verify_keeps_commas_in_srcset_data_url(dist):
    _write(dist, "view/docs/guide/index.html", _page(
        '<img srcset="data:image/png;base64,iVBORw0KGgo= 1x, data:image/png;base64,iVBORw0KGgo= 2x">'))
    assert verify_dist(dist, secret_values=[]) == []


def test_verify_ignores_hidden_download_links(dist):
    _write(dist, "view/docs/guide/index.html", _page(
        '<a href="/view/api/repos/docs/download?path=guide.md&amp;ref=main">원본</a>'
        '<a href="/view/api/repos/docs/download?path=a%20b.md">원본</a>'))
    assert verify_dist(dist, secret_values=[]) == []


def test_verify_flags_broken_document_link_in_body(dist):
    _write(dist, "view/docs/guide/index.html", _page(
        '<a href="/view/api/repos/docs/download?path=guide.md">원본</a>'
        '<a href="/view/docs/no-such/">깨진 문서 링크</a>'))
    violations = verify_dist(dist, secret_values=[])
    assert any("no-such" in v for v in violations)
    assert not any("download" in v for v in violations)


def test_verify_does_not_exempt_other_api_links(dist):
    _write(dist, "view/docs/guide/index.html", _page('<a href="/view/api/repos/docs/search?q=x">s</a>'))
    assert any("search" in v for v in verify_dist(dist, secret_values=[]))


def test_verify_flags_exclude_marker_in_content_and_path(dist):
    _write(dist, "view/docs/body.html", _page("UNIQUE-EXCLUDED-MARKER-1"))
    _write(dist, "view/docs/secret-doc-2/index.html", _page("x"))
    violations = verify_dist(dist, secret_values=[], exclude_markers=["UNIQUE-EXCLUDED-MARKER-1", "secret-doc-2"])
    assert any("body.html" in v for v in violations)
    assert any("secret-doc-2" in v for v in violations)


def test_verify_ignores_blank_exclude_markers(dist):
    assert verify_dist(dist, secret_values=[], exclude_markers=["", ""]) == []


def test_verify_does_not_echo_secret_in_messages(dist):
    _write(dist, "view/docs/leak.txt", f"x {FAKE_TOKEN} y")
    _write(dist, "view/docs/leak.html", _page(f'<a href="/view/{FAKE_TOKEN}/">x</a>'))
    for message in verify_dist(dist, secret_values=[FAKE_TOKEN]):
        assert FAKE_TOKEN not in message


def test_verify_handles_non_utf8_html(dist):
    (dist / "view/docs/bin.html").write_bytes(b"\xff\xfe<a href='/view/none/'>")
    verify_dist(dist, secret_values=[])


def test_verify_flags_directory_symlink(dist, tmp_path):
    outside = tmp_path / "outside"
    _write(outside, "leak.txt", f"token={FAKE_TOKEN}")
    (dist / "view/linked").symlink_to(outside, target_is_directory=True)
    violations = verify_dist(dist, secret_values=[FAKE_TOKEN])
    assert any("심볼릭 링크가 있다: view/linked" in v for v in violations)


def test_verify_flags_expand_path_on_repository_page(dist):
    _write(dist, "view/docs/index.html", _page('<div class="tree" id="file-tree" data-expand-path="a/b"></div>'))
    violations = verify_dist(dist, secret_values=[])
    assert any("data-expand-path" in v and "view/docs/index.html" in v for v in violations)


def test_verify_allows_expand_path_text_outside_repository_pages(dist):
    _write(dist, "view/docs/guide/index.html", _page('<code>data-expand-path</code>'))
    assert verify_dist(dist, secret_values=[]) == []

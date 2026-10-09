import base64
import subprocess
from unittest.mock import MagicMock

import pytest

from synctx_common.config import HtmlEmbedConfig
from synctx_common.html_bundle import BundleError, bundle, repo_reader

ORIGIN = "https://cdn.synctx.local"
POLICY = HtmlEmbedConfig(enabled=True, allowed_origins=[ORIGIN])
PNG = b"\x89PNG\r\n\x1a\n"


def reader(files):
    def read(path):
        if path not in files:
            raise FileNotFoundError(path)
        data = files[path]
        return data.encode("utf-8") if isinstance(data, str) else data
    return read


def page(head="", body=""):
    return f"<!DOCTYPE html><html><head><title>모션</title>{head}</head><body>{body}</body></html>"


def b64(data):
    return base64.b64encode(data).decode("ascii")


def test_inlines_stylesheet_and_script():
    files = {
        "m/index.html": page('<link rel="stylesheet" href="motion.css">', '<script src="motion.js"></script>'),
        "m/motion.css": "body{color:red}",
        "m/motion.js": "window.X=1;",
    }
    result = bundle(reader(files), "m/index.html", POLICY)
    assert "<style>body{color:red}</style>" in result.html
    assert "<script>window.X=1;</script>" in result.html
    assert 'href="motion.css"' not in result.html and 'src="motion.js"' not in result.html
    assert result.files == ("m/index.html", "m/motion.css", "m/motion.js")
    assert result.title == "모션"
    assert result.size == len(result.html.encode("utf-8"))


def test_closing_tags_inside_inlined_code_are_escaped():
    files = {
        "m/index.html": page('<link rel="stylesheet" href="a.css">', '<script src="a.js"></script>'),
        "m/a.css": "a::after{content:'</style>'}",
        "m/a.js": "var s='</script><!--';",
    }
    html = bundle(reader(files), "m/index.html", POLICY).html
    assert "<\\/script>" in html and "<\\!--" in html and "<\\/style>" in html
    assert html.count("</script>") == 1


def test_image_becomes_data_uri():
    files = {"m/index.html": page(body='<img src="logo.png" alt="로고">'), "m/logo.png": PNG}
    html = bundle(reader(files), "m/index.html", POLICY).html
    assert f'src="data:image/png;base64,{b64(PNG)}"' in html
    assert 'alt="로고"' in html


def test_css_url_is_relative_to_css_file():
    files = {
        "m/index.html": page('<link rel="stylesheet" href="css/site.css">'),
        "m/css/site.css": "body{background:url(../img/bg.png)}",
        "m/img/bg.png": PNG,
    }
    result = bundle(reader(files), "m/index.html", POLICY)
    assert f'url("data:image/png;base64,{b64(PNG)}")' in result.html
    assert "m/img/bg.png" in result.files


def test_inline_style_and_style_attribute_are_relative_to_entry():
    files = {
        "m/index.html": page("<style>.a{background:url('img/a.png')}</style>", '<div style="background:url(img/a.png)"></div>'),
        "m/img/a.png": PNG,
    }
    html = bundle(reader(files), "m/index.html", POLICY).html
    assert html.count(f"data:image/png;base64,{b64(PNG)}") == 2


def test_font_becomes_data_uri():
    files = {
        "m/index.html": page('<link rel="stylesheet" href="f.css">'),
        "m/f.css": "@font-face{font-family:x;src:url(fonts/x.woff2) format('woff2')}",
        "m/fonts/x.woff2": b"wOF2",
    }
    html = bundle(reader(files), "m/index.html", POLICY).html
    assert f'url("data:font/woff2;base64,{b64(b"wOF2")}")' in html


def test_allowed_origin_is_kept_and_others_rejected():
    ok = {"m/index.html": page(body=f'<img src="{ORIGIN}:443/a.png"><img src="{ORIGIN}/b.png">')}
    html = bundle(reader(ok), "m/index.html", POLICY).html
    assert f'src="{ORIGIN}:443/a.png"' in html
    for url in ("https://other.synctx.local/a.png", f"{ORIGIN}:8443/a.png", "http://cdn.synctx.local/a.png", "//cdn.synctx.local/a.png"):
        files = {"m/index.html": page(body=f'<img src="{url}">')}
        with pytest.raises(BundleError) as exc:
            bundle(reader(files), "m/index.html", POLICY)
        assert exc.value.reason == "disallowed_host", url


@pytest.mark.parametrize("url", ["https://[", "https://[::1", "https://cdn.synctx.local:99999/a.png"])
def test_malformed_absolute_url_becomes_bundle_error(url):
    files = {"m/index.html": page(body=f'<img src="{url}">')}
    with pytest.raises(BundleError) as exc:
        bundle(reader(files), "m/index.html", POLICY)
    assert exc.value.reason == "disallowed_host"


def test_outside_repo_is_rejected():
    files = {"m/index.html": page(body='<script src="../../x.js"></script>')}
    with pytest.raises(BundleError) as exc:
        bundle(reader(files), "m/index.html", POLICY)
    assert exc.value.reason == "outside_repo"
    assert exc.value.files == ("m/index.html",)


def test_missing_files_are_collected():
    files = {"m/index.html": page('<link rel="stylesheet" href="a.css">', '<script src="b.js"></script>')}
    with pytest.raises(BundleError) as exc:
        bundle(reader(files), "m/index.html", POLICY)
    assert exc.value.reason == "missing_file"
    assert exc.value.missing == ("m/a.css", "m/b.js")
    assert exc.value.files == ("m/index.html",)


def test_missing_entry():
    with pytest.raises(BundleError) as exc:
        bundle(reader({}), "m/index.html", POLICY)
    assert exc.value.reason == "missing_file"
    assert exc.value.missing == ("m/index.html",)


def test_css_import_is_unsupported():
    files = {"m/index.html": page('<link rel="stylesheet" href="a.css">'), "m/a.css": "@import 'b.css';"}
    with pytest.raises(BundleError) as exc:
        bundle(reader(files), "m/index.html", POLICY)
    assert exc.value.reason == "unsupported"


def test_non_utf8_text_is_unsupported():
    files = {"m/index.html": page('<link rel="stylesheet" href="a.css">'), "m/a.css": "/* 한글 */".encode("cp949")}
    with pytest.raises(BundleError) as exc:
        bundle(reader(files), "m/index.html", POLICY)
    assert exc.value.reason == "unsupported"
    assert "UTF-8" in str(exc.value)


def test_query_and_fragment_are_ignored():
    files = {
        "m/index.html": page('<link rel="stylesheet" href="motion.css?v=2">'),
        "m/motion.css": "@font-face{src:url(f.woff2#iefix)}",
        "m/f.woff2": b"wOF2",
    }
    result = bundle(reader(files), "m/index.html", POLICY)
    assert result.files == ("m/f.woff2", "m/index.html", "m/motion.css")


def test_korean_and_percent_encoded_names():
    files = {"m/index.html": page(body='<img src="이미지.png"><img src="%EC%9D%B4%EB%AF%B8%EC%A7%80.png">'), "m/이미지.png": PNG}
    html = bundle(reader(files), "m/index.html", POLICY).html
    assert html.count(f"data:image/png;base64,{b64(PNG)}") == 2


def test_uppercase_and_unquoted_markup():
    files = {"m/index.html": "<HTML><BODY><SCRIPT SRC=motion.js></SCRIPT></BODY></HTML>", "m/motion.js": "window.X=1;"}
    html = bundle(reader(files), "m/index.html", POLICY).html
    assert "<script>window.X=1;</script>" in html


def test_svg_self_closing_tags_are_preserved():
    files = {"m/index.html": page(body='<svg viewBox="0 0 10 10"><path d="M0 0L10 10"/></svg>')}
    html = bundle(reader(files), "m/index.html", POLICY).html
    assert '<path d="M0 0L10 10" />' in html


def test_icon_link_becomes_data_uri_and_other_relative_links_are_unsupported():
    files = {"m/index.html": page('<link rel="icon" href="f.ico">'), "m/f.ico": b"ico"}
    assert "data:image/x-icon;base64," in bundle(reader(files), "m/index.html", POLICY).html
    files = {"m/index.html": page('<link rel="preload" href="a.js">')}
    with pytest.raises(BundleError) as exc:
        bundle(reader(files), "m/index.html", POLICY)
    assert exc.value.reason == "unsupported"


def test_bundle_is_deterministic():
    files = {"m/index.html": page('<link rel="stylesheet" href="a.css">', '<img src="a.png">'), "m/a.css": "p{}", "m/a.png": PNG}
    first = bundle(reader(files), "m/index.html", POLICY)
    second = bundle(reader(files), "m/index.html", POLICY)
    assert (first.html, first.sha256) == (second.html, second.sha256)


def test_repo_reader_maps_git_errors_to_file_not_found():
    manager = MagicMock()
    manager.read_file.side_effect = subprocess.CalledProcessError(128, ["git", "show"])
    with pytest.raises(FileNotFoundError):
        repo_reader(manager, "main")("m/a.js")
    manager.read_file.side_effect = None
    manager.read_file.return_value = b"x"
    assert repo_reader(manager, "main")("m/a.js") == b"x"
    manager.read_file.assert_called_with("m/a.js", ref="main")

import base64
import subprocess
from unittest.mock import MagicMock

import pytest

from synctx_common.config import HtmlEmbedConfig
from synctx_common.html_bundle import BundleError, bundle, repo_reader, resolve_path

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
    manager.resolve_ref.return_value = "origin/main"
    manager.read_resolved.side_effect = subprocess.CalledProcessError(128, ["git", "show"])
    with pytest.raises(FileNotFoundError):
        repo_reader(manager, "main")("m/a.js")
    manager.read_resolved.side_effect = None
    manager.read_resolved.return_value = b"x"
    assert repo_reader(manager, "main")("m/a.js") == b"x"
    manager.read_resolved.assert_called_with("m/a.js", "origin/main")


@pytest.mark.parametrize("reference", ["a%00.js", "a%0a.js", "a%7f.js"])
def test_control_characters_in_decoded_path_are_block_errors(reference):
    with pytest.raises(BundleError) as exc:
        resolve_path("m/index.html", reference)
    assert exc.value.reason == "unsupported"


def test_repo_reader_maps_invalid_path_to_bundle_error():
    manager = MagicMock()
    manager.resolve_ref.return_value = "origin/main"
    manager.read_resolved.side_effect = ValueError("embedded null byte")
    with pytest.raises(BundleError) as exc:
        repo_reader(manager)("m/a.js")
    assert exc.value.reason == "unsupported"


def test_repo_reader_resolves_ref_once_per_reader():
    manager = MagicMock()
    manager.resolve_ref.return_value = "origin/feature"
    manager.read_resolved.return_value = b"x"
    read = repo_reader(manager, "feature")
    read("m/a.js")
    read("m/b.css")
    manager.resolve_ref.assert_called_once_with("feature")
    assert manager.read_resolved.call_count == 2


def test_repo_reader_does_not_report_fetch_failure_as_missing_file():
    manager = MagicMock()
    manager.resolve_ref.side_effect = subprocess.CalledProcessError(128, ["git", "fetch"])
    with pytest.raises(subprocess.CalledProcessError):
        repo_reader(manager, "feature")("m/a.js")


from synctx_common.html_bundle import csp_meta

CSP_WITH_ORIGIN = (
    '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
    "script-src 'unsafe-inline' https://cdn.synctx.local; style-src 'unsafe-inline' https://cdn.synctx.local; "
    "img-src data: https://cdn.synctx.local; font-src data: https://cdn.synctx.local; connect-src https://cdn.synctx.local; "
    "frame-src 'none'; worker-src 'none'; form-action 'none'; base-uri 'none'\">"
)


def test_csp_meta_text():
    assert csp_meta([ORIGIN]) == CSP_WITH_ORIGIN
    empty = csp_meta([])
    assert "connect-src 'none';" in empty
    assert "script-src 'unsafe-inline';" in empty
    assert "blob:" not in empty and "media-src" not in empty


def test_csp_meta_comes_right_after_doctype_and_before_scripts():
    files = {"m/index.html": page('<script>window.a=1</script><link rel="stylesheet" href="a.css">'), "m/a.css": "p{}"}
    html = bundle(reader(files), "m/index.html", POLICY).html
    assert html.startswith("<!DOCTYPE html>" + CSP_WITH_ORIGIN)


@pytest.mark.parametrize(
    "source",
    [
        "<script>window.a=1</script><!DOCTYPE html><p>x</p>",
        "<!-- c --><script>window.a=1</script><!DOCTYPE html>",
        "<p>x</p><!DOCTYPE html><script>window.a=1</script>",
    ],
)
def test_trailing_doctype_does_not_move_csp_after_scripts(source):
    html = bundle(reader({"m/index.html": source}), "m/index.html", POLICY).html
    assert html.startswith(CSP_WITH_ORIGIN)


@pytest.mark.parametrize("prefix", ["", "\n  ", "\t\r\n\f "])
def test_html_whitespace_before_doctype_keeps_doctype_first(prefix):
    html = bundle(reader({"m/index.html": prefix + "<!DOCTYPE html><p>x</p>"}), "m/index.html", POLICY).html
    assert html.startswith(prefix + "<!DOCTYPE html>" + CSP_WITH_ORIGIN)


@pytest.mark.parametrize(
    "source",
    [
        "\u3000<!DOCTYPE html><script>window.a=1</script>",
        "\u00a0<!DOCTYPE html><script>window.a=1</script>",
        "\x1c<!DOCTYPE html><script>window.a=1</script>",
        "<!-- 머리말 -->\n<!DOCTYPE html><script>window.a=1</script>",
        "<!--><script>window.a=1</script>--><!DOCTYPE html><p>x</p>",
    ],
)
def test_csp_meta_goes_first_unless_doctype_leads_after_html_whitespace(source):
    html = bundle(reader({"m/index.html": source}), "m/index.html", POLICY).html
    assert html.startswith(CSP_WITH_ORIGIN)


def test_csp_meta_is_first_without_doctype():
    html = bundle(reader({"m/index.html": "<p>x</p>"}), "m/index.html", HtmlEmbedConfig(enabled=True)).html
    assert html.startswith('<meta http-equiv="Content-Security-Policy"')
    assert "connect-src 'none'" in html


def test_defer_scripts_move_to_body_end_in_order():
    files = {
        "m/index.html": page('<script defer src="a.js"></script><script defer src="b.js"></script>', "<p>x</p><script>window.inline=1</script>"),
        "m/a.js": "window.order=['a'];",
        "m/b.js": "window.order.push('b');",
    }
    html = bundle(reader(files), "m/index.html", POLICY).html
    inline = html.index("window.inline=1")
    a = html.index("window.order=['a']")
    b = html.index("window.order.push('b')")
    assert inline < a < b < html.index("</body>")
    assert "defer" not in html


def test_defer_without_body_end_is_appended():
    files = {"m/index.html": '<script defer src="a.js"></script><p>x</p>', "m/a.js": "window.a=1;"}
    html = bundle(reader(files), "m/index.html", POLICY).html
    assert html.endswith("<p>x</p><script>window.a=1;</script>")


@pytest.mark.parametrize(
    "extra",
    ['<script type="module">window.m=1</script>', f'<script defer src="{ORIGIN}/lib.js"></script>'],
)
def test_defer_mixed_with_module_or_external_defer_is_unsupported(extra):
    files = {"m/index.html": page('<script defer src="a.js"></script>' + extra), "m/a.js": "window.a=1;"}
    with pytest.raises(BundleError) as exc:
        bundle(reader(files), "m/index.html", POLICY)
    assert exc.value.reason == "unsupported"


@pytest.mark.parametrize("src", ["a.js", f"{ORIGIN}/a.js"])
def test_async_is_unsupported(src):
    files = {"m/index.html": page(f'<script async src="{src}"></script>'), "m/a.js": "x"}
    with pytest.raises(BundleError) as exc:
        bundle(reader(files), "m/index.html", POLICY)
    assert exc.value.reason == "unsupported"


def test_module_scripts():
    files = {"m/index.html": page(body='<script type="module" src="app.mjs"></script><script type="module">window.b=2</script>'), "m/app.mjs": "window.a=1;"}
    html = bundle(reader(files), "m/index.html", POLICY).html
    assert '<script type="module">window.a=1;</script>' in html
    assert '<script type="module">window.b=2</script>' in html


@pytest.mark.parametrize(
    "markup,extra",
    [
        ('<script type="module" src="app.mjs"></script>', {"m/app.mjs": "import x from './x.js';"}),
        ('<script type="module">import("./x.js")</script>', {}),
        ('<script type="module">import x from /* 주석 */ "./x.js"</script>', {}),
        ('<script type="module">import x from // 줄 주석\n "./x.js"</script>', {}),
        ('<script type="module">import(`./x.js`)</script>', {}),
        ('<script type="module">import(`${base}/x.js`)</script>', {}),
        ('<script type="module">const n = "./x.js"; import(n)</script>', {}),
        ('<script type="module">import "./side-effect.js"</script>', {}),
        ('<script type="module">export * from "./x.js"</script>', {}),
        ('<script type="module">import x from "lodash"</script>', {}),
        ('<script type="module">import x from "https://other.synctx.local/x.mjs"</script>', {}),
        ("<script>import('./x.js')</script>", {}),
        ('<script src="app.js"></script>', {"m/app.js": "import(/* c */ './x.js')"}),
        (f'<script type="module">import("{ORIGIN}/x.mjs" && "./x.js")</script>', {}),
        (f'<script type="module">import("{ORIGIN}/x.mjs" || "./x.js")</script>', {}),
        (f'<script type="module">import("{ORIGIN}/x.mjs" + "?v=1")</script>', {}),
        (f'<script type="module">import("{ORIGIN}/x.mjs".replace("{ORIGIN}", "."))</script>', {}),
        (f'<script type="module">import(`{ORIGIN}/x.mjs`.trim())</script>', {}),
        (f'<script type="module">import(cond ? "{ORIGIN}/x.mjs" : "./x.js")</script>', {}),
        (f"<script>import('{ORIGIN}/x.mjs' /* c */ + './y')</script>", {}),
        (f'<script type="module">import("{ORIGIN}/x.mjs" && "./x.js" && "")</script>', {}),
        (f'<script type="module">import("{ORIGIN}/a\\".mjs" + "./x.js")</script>', {}),
    ],
)
def test_unloadable_imports_are_unsupported(markup, extra):
    files = {"m/index.html": page(body=markup), **extra}
    with pytest.raises(BundleError) as exc:
        bundle(reader(files), "m/index.html", POLICY)
    assert exc.value.reason == "unsupported"


@pytest.mark.parametrize(
    "markup",
    [
        f'<script type="module">import x from "{ORIGIN}/x.mjs"; import("{ORIGIN}/y.mjs")</script>',
        f'<script type="module">import(/* c */ "{ORIGIN}/y.mjs" /* c */)</script>',
        f'<script type="module">import("{ORIGIN}/y.json", {{ with: {{ type: "json" }} }})</script>',
        f"<script>import(`{ORIGIN}/y.mjs`)</script>",
        '<script type="module">const s = Array.from("abc"); const meta = import.meta.url;</script>',
        "<script>var important = 1; obj.import('x');</script>",
        '<script type="application/json">{"import": "./x.js"}</script>',
    ],
)
def test_loadable_imports_and_lookalikes_are_allowed(markup):
    files = {"m/index.html": page(body=markup)}
    assert "<script" in bundle(reader(files), "m/index.html", POLICY).html


@pytest.mark.parametrize(
    "markup",
    [
        '<video src="a.mp4"></video>',
        '<audio src="a.mp3"></audio>',
        '<iframe src="a.html"></iframe>',
        '<iframe srcdoc="x"></iframe>',
        '<object data="a.pdf"></object>',
        '<svg><image href="a.png"/></svg>',
        '<base href="/">',
    ],
)
def test_media_and_frames_are_unsupported(markup):
    files = {"m/index.html": page(body=markup)}
    with pytest.raises(BundleError) as exc:
        bundle(reader(files), "m/index.html", POLICY)
    assert exc.value.reason == "unsupported"


def test_svg_use_fragment_is_allowed():
    files = {"m/index.html": page(body='<svg><use href="#icon"/></svg>')}
    assert '<use href="#icon" />' in bundle(reader(files), "m/index.html", POLICY).html


def test_too_large():
    files = {"m/index.html": page(body="x" * 200)}
    with pytest.raises(BundleError) as exc:
        bundle(reader(files), "m/index.html", HtmlEmbedConfig(enabled=True, max_bundle_bytes=100))
    assert exc.value.reason == "too_large"
    assert exc.value.files == ("m/index.html",)


def test_utf8_bom_is_removed_from_inlined_text():
    files = {
        "m/index.html": "﻿" + page('<link rel="stylesheet" href="a.css">', '<script src="a.js"></script>'),
        "m/a.css": "﻿:root{--x:1}",
        "m/a.js": "﻿window.a=1;",
    }
    html = bundle(reader(files), "m/index.html", POLICY).html
    assert "﻿" not in html
    assert "<style>:root{--x:1}</style>" in html
    assert html.startswith("<!DOCTYPE html>" + CSP_WITH_ORIGIN)

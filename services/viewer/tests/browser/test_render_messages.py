from urllib.parse import quote

INSTALL_BUTTON = """([type, payload]) => {
  window.replies = [];
  window.addEventListener('message', (e) => { if (e.source === window.parent) window.replies.push(e.data); });
  const button = document.createElement('button');
  button.id = 'send';
  button.textContent = 'send';
  button.addEventListener('click', () => window.parent.postMessage(Object.assign({ type }, payload), '*'));
  document.body.appendChild(button);
}"""


def _render(viewer_server, page):
    page.goto(f"{viewer_server.base_url}/view/{viewer_server.repo_id}/motion/index.html?mode=render")
    element = page.locator("[data-render-frame]")
    frame = element.element_handle().content_frame()
    frame.get_by_role("button", name="증가").wait_for()
    return element, frame


def test_refresh_request_reports_same_commit_without_replacing_frame(viewer_server, page):
    element, frame = _render(viewer_server, page)
    source = element.get_attribute("src")
    frame.evaluate(INSTALL_BUTTON, ["synctx:render-refresh", {"hash": "#board?s=due"}])
    frame.locator("#send").click()
    frame.wait_for_function("() => window.replies.length > 0")
    assert frame.evaluate("window.replies") == [{"type": "synctx:render-refresh-result", "changed": False}]
    assert element.get_attribute("src") == source


def test_refresh_reloads_screen_when_commit_differs_and_keeps_hash(viewer_server, page):
    element, frame = _render(viewer_server, page)
    main_sha = page.get_attribute("[data-html-render]", "data-render-sha")
    assert len(main_sha) == 40
    page.evaluate("document.querySelector('[data-html-render]').setAttribute('data-render-sha', 'old')")
    frame.evaluate(INSTALL_BUTTON, ["synctx:render-refresh", {"hash": "#board?s=due"}])
    with page.expect_navigation():
        frame.locator("#send").click()
    page.wait_for_function("() => document.querySelector('[data-render-frame]').src.endsWith('#board?s=due')")
    assert page.get_attribute("[data-html-render]", "data-render-sha") == main_sha
    assert page.evaluate("sessionStorage.getItem('synctx:render-frame-hash')") is None


def test_open_request_navigates_to_document_in_same_repo(viewer_server, page):
    element, frame = _render(viewer_server, page)
    frame.evaluate(INSTALL_BUTTON, ["synctx:open", {"path": "/doc.md", "newWindow": False}])
    with page.expect_navigation():
        frame.locator("#send").click()
    assert page.url == f"{viewer_server.base_url}/view/{viewer_server.repo_id}/doc.md"


def test_open_request_new_window_and_rejects_bad_paths(viewer_server, page):
    element, frame = _render(viewer_server, page)
    frame.evaluate(INSTALL_BUTTON, ["synctx:open", {"path": "/doc.md", "newWindow": True}])
    with page.context.expect_page() as opened:
        frame.locator("#send").click()
    popup = opened.value
    popup.wait_for_load_state()
    assert popup.url == f"{viewer_server.base_url}/view/{viewer_server.repo_id}/{quote('doc.md')}"
    assert popup.evaluate("window.opener") is None
    frame.evaluate("document.getElementById('send').remove()")
    frame.evaluate(INSTALL_BUTTON, ["synctx:open", {"path": "//evil.example/x.md", "newWindow": False}])
    frame.locator("#send").click()
    frame.wait_for_function("() => window.replies.length > 0")
    assert frame.evaluate("window.replies")[0]["error"]



def test_open_request_new_window_html_opens_file_itself(viewer_server, page):
    element, frame = _render(viewer_server, page)
    frame.evaluate(INSTALL_BUTTON, ["synctx:open", {"path": "/motion/index.html", "newWindow": True}])
    with page.context.expect_page() as opened:
        frame.locator("#send").click()
    popup = opened.value
    popup.wait_for_load_state()
    assert f"/view/open/" in popup.url and popup.url.endswith(f"/{viewer_server.repo_id}/motion/index.html")
    assert popup.locator("[data-render-frame]").count() == 0
    popup.get_by_role("button", name="증가").wait_for()
    assert popup.evaluate("window.opener") is None


def test_open_url_opens_only_github_in_new_window(viewer_server, page):
    page.context.route("https://github.com/**", lambda r: r.fulfill(body="<title>gh</title>", content_type="text/html"))
    element, frame = _render(viewer_server, page)
    frame.evaluate(INSTALL_BUTTON, ["synctx:open-url", {"url": "https://github.com/o/r/pull/1"}])
    with page.context.expect_page() as opened:
        frame.locator("#send").click()
    popup = opened.value
    popup.wait_for_load_state()
    assert popup.url == "https://github.com/o/r/pull/1"
    frame.evaluate("document.getElementById('send').remove()")
    frame.evaluate(INSTALL_BUTTON, ["synctx:open-url", {"url": "https://evil.example/x"}])
    frame.locator("#send").click()
    frame.wait_for_function("() => window.replies.some(r => r.type === 'synctx:open-url-result')")
    assert [r for r in frame.evaluate("window.replies") if r["type"] == "synctx:open-url-result"][-1]["error"]

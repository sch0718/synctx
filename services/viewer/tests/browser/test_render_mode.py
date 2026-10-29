import base64
from urllib.parse import quote, urlsplit


def test_render_mode_runs_html_and_relative_link_same_snapshot(viewer_server, page):
    from playwright.sync_api import expect

    page.goto(f"{viewer_server.base_url}/view/{viewer_server.repo_id}/motion/index.html"
              f"?mode=render&ref={quote(viewer_server.ref, safe='')}")
    frame_element = page.locator("[data-render-frame]")
    source = frame_element.get_attribute("src")
    token = source.split("/render/", 1)[1].split("/", 1)[0]
    fields = token.split(".")
    assert base64.urlsafe_b64decode(fields[2] + "=" * (-len(fields[2]) % 4)).decode() == viewer_server.ref
    assert fields[3] == viewer_server.sha
    frame = frame_element.element_handle().content_frame()
    frame.get_by_role("button", name="증가").click()
    expect(frame.locator("#counter")).to_have_text("1")
    assert frame.evaluate("window.executionMarker") == "executed"
    destination = viewer_server.base_url + source.rsplit("/", 1)[0] + "/detail.html"
    with page.expect_response(destination) as received:
        frame.get_by_role("link", name="상세", exact=True).click()
    assert received.value.status == 200
    headers = received.value.headers
    assert "sandbox" in headers.get("content-security-policy", "")
    assert "frame-ancestors" not in headers.get("content-security-policy", "")
    assert "x-frame-options" not in headers
    nested = frame.frame_locator('iframe[name="pf"]')
    expect(nested.locator("#snapshot")).to_have_text("selected snapshot")
    child = frame.child_frames[0]
    assert child.url == destination
    assert child.evaluate("window.executionMarker") == "executed"
    assert frame.evaluate("window.origin") == "null"


def test_tampered_token_returns_403_without_execution(viewer_server, page):
    page.goto(f"{viewer_server.base_url}/view/{viewer_server.repo_id}/motion/index.html?mode=render")
    element = page.locator("[data-render-frame]")
    frame = element.element_handle().content_frame()
    frame.get_by_role("button", name="증가").wait_for()
    source = element.get_attribute("src")
    token = source.split("/render/", 1)[1].split("/", 1)[0]
    prefix, signature = token.rsplit(".", 1)
    tampered = prefix + "." + ("A" if signature[0] != "A" else "B") + signature[1:]
    response = frame.goto(viewer_server.base_url + source.replace(token, tampered, 1))
    assert response.status == 403
    assert "executionMarker" not in response.text()
    assert frame.evaluate("typeof window.executionMarker") == "undefined"
    assert frame.locator("#increment").count() == 0


def test_expired_token_recovers(viewer_server_factory, page):
    from playwright.sync_api import expect

    server = viewer_server_factory(ttl_seconds=3)
    original = f"{server.base_url}/view/{server.repo_id}/motion/index.html?mode=render"
    page.goto(original)
    element = page.locator("[data-render-frame]")
    source = element.get_attribute("src")
    token = source.split("/render/", 1)[1].split("/", 1)[0]
    frame = element.element_handle().content_frame()
    frame.get_by_role("button", name="증가").wait_for()
    page.wait_for_function("expiry => Date.now() >= expiry * 1000", arg=int(token.split(".")[1]))
    destination = server.base_url + source.rsplit("/", 1)[0] + "/detail.html"
    with page.expect_response(destination) as received:
        frame.get_by_role("link", name="상세", exact=True).click()
    assert received.value.status == 403
    nested = frame.frame_locator('iframe[name="pf"]')
    expect(nested.locator("body")).to_contain_text("원래 Viewer 화면을 새로 고치세요")
    child = frame.child_frames[0]
    assert child.evaluate("typeof window.executionMarker") == "undefined"
    assert "executionMarker" not in received.value.text()
    page.reload()
    assert page.url == original
    refreshed = page.locator("[data-render-frame]")
    fresh_source = refreshed.get_attribute("src")
    assert fresh_source != source
    assert urlsplit(fresh_source).path.rsplit("/", 2)[-2:] == ["motion", "index.html"]
    frame = refreshed.element_handle().content_frame()
    frame.get_by_role("button", name="증가").click()
    expect(frame.locator("#counter")).to_have_text("1")
    frame.get_by_role("link", name="상세", exact=True).click()
    expect(frame.frame_locator('iframe[name="pf"]').locator("#snapshot")).to_have_text("main snapshot")


def test_block_zoom_overlay_esc_restores_focus(viewer_server, page):
    from playwright.sync_api import expect

    page.goto(f"{viewer_server.base_url}/view/{viewer_server.repo_id}/doc.md")
    container = page.locator("[data-html-embed]")
    element = container.locator("iframe.synctx-html")
    frame = element.element_handle().content_frame()
    frame.get_by_role("button", name="증가").click()
    expect(frame.locator("#counter")).to_have_text("1")
    frame.evaluate("window.playbackIdentity = 'retained'")
    opener = container.get_by_role("button", name="크게 보기")
    opener.click()
    expect(container).to_have_attribute("data-embed-expanded", "true")
    expect(container.get_by_role("button", name="닫기", exact=True)).to_be_visible()
    assert frame.evaluate("window.playbackIdentity") == "retained"
    expect(frame.locator("#counter")).to_have_text("1")
    page.keyboard.press("Escape")
    expect(container).not_to_have_attribute("data-embed-expanded", "true")
    expect(opener).to_be_focused()
    assert frame.evaluate("window.playbackIdentity") == "retained"
    expect(frame.locator("#counter")).to_have_text("1")

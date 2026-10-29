def test_viewer_serves_synctx_html_block(viewer_server, page):
    response = page.goto(f"{viewer_server.base_url}/view/{viewer_server.repo_id}/doc.md")
    assert response.status == 200
    assert page.locator("iframe").count() == 1
    frame = page.frame_locator("iframe.synctx-html")
    frame.get_by_role("button", name="증가").click()
    assert frame.locator("#counter").inner_text() == "1"

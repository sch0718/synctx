import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit

import pytest


@pytest.fixture
def request_sink():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

        def log_message(self, format, *args):
            pass

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        try:
            thread.start()
            yield f"http://127.0.0.1:{server.server_port}", requests
        finally:
            server.shutdown()
            thread.join(timeout=5)
            assert not thread.is_alive(), "CSP 수신 서버가 종료되지 않았습니다"


def test_iframe_origin_is_opaque(viewer_server, page):
    page.goto(f"{viewer_server.base_url}/view/{viewer_server.repo_id}/doc.md")
    frame = page.frame_locator("iframe.synctx-html")
    frame.get_by_role("button", name="증가").wait_for()
    result = frame.locator("body").evaluate("""() => {
        let parent;
        try { parent = window.parent.document.title; }
        catch (error) { parent = error.name; }
        return {origin: location.origin, parent};
    }""")
    assert result == {"origin": "null", "parent": "SecurityError"}


def test_iframe_cannot_read_viewer_storage(viewer_server, page):
    page.goto(f"{viewer_server.base_url}/view/{viewer_server.repo_id}/doc.md")
    page.evaluate("""() => {
        localStorage.setItem('browser-secret', 'parent-only');
        document.cookie = 'browser-secret=parent-only; path=/';
    }""")
    assert page.evaluate("localStorage.getItem('browser-secret')") == "parent-only"
    assert "browser-secret=parent-only" in page.evaluate("document.cookie")
    frame = page.frame_locator("iframe.synctx-html")
    frame.get_by_role("button", name="증가").wait_for()
    result = frame.locator("body").evaluate("""() => {
        const read = fn => { try { return fn(); } catch (error) { return error.name; } };
        return {
            storage: read(() => localStorage.getItem('browser-secret')),
            cookie: read(() => document.cookie),
            parentStorage: read(() => window.parent.localStorage.getItem('browser-secret'))
        };
    }""")
    assert result == {"storage": "SecurityError", "cookie": "SecurityError", "parentStorage": "SecurityError"}


def test_external_requests_blocked_by_csp(viewer_server, page, request_sink):
    destination, requests = request_sink
    page.goto(f"{viewer_server.base_url}/view/{viewer_server.repo_id}/doc.md")
    frame = page.frame_locator("iframe.synctx-html")
    frame.get_by_role("button", name="증가").wait_for()
    result = frame.locator("body").evaluate("""async (_, destination) => {
        const violations = [];
        let fetchResult = 'pending';
        let imageResult = 'pending';
        let finish;
        const blocked = new Promise(resolve => { finish = resolve; });
        const record = event => {
            violations.push({directive: event.effectiveDirective, uri: event.blockedURI,
                             policy: event.originalPolicy});
            const metaDirectives = new Set(violations.filter(item =>
                item.policy.includes("connect-src 'none'")).map(item => item.directive));
            if (metaDirectives.has('connect-src') && metaDirectives.has('img-src')) finish();
        };
        document.addEventListener('securitypolicyviolation', record);
        const fetching = fetch(destination + '/fetch').then(
            () => { fetchResult = 'allowed'; }, () => { fetchResult = 'blocked'; });
        const image = document.createElement('img');
        const imaging = new Promise(resolve => {
            image.onload = () => { imageResult = 'allowed'; resolve(); };
            image.onerror = () => { imageResult = 'blocked'; resolve(); };
        });
        image.src = destination + '/image';
        document.body.append(image);
        let timer;
        const timeout = new Promise(resolve => { timer = setTimeout(resolve, 3000); });
        await Promise.race([Promise.all([blocked, fetching, imaging]), timeout]);
        clearTimeout(timer);
        document.removeEventListener('securitypolicyviolation', record);
        image.remove();
        return {violations, fetch: fetchResult, image: imageResult};
    }""", destination)
    assert result["fetch"] == "blocked"
    assert result["image"] == "blocked"
    assert {item["directive"] for item in result["violations"]} == {"connect-src", "img-src"}
    assert {item["directive"] for item in result["violations"]
            if "connect-src 'none'" in item["policy"]} == {"connect-src", "img-src"}
    assert {(urlsplit(item["uri"]).scheme, urlsplit(item["uri"]).netloc)
            for item in result["violations"]} == {("http", urlsplit(destination).netloc)}
    assert requests == []

import importlib
import socket
import subprocess
import threading
import time
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

import pytest
import yaml


def _explicitly_selected(config):
    directory = Path(__file__).parent.resolve()
    return any(Path(arg.split("::", 1)[0]).resolve().is_relative_to(directory) for arg in config.args)


def pytest_collection_modifyitems(config, items):
    if not _explicitly_selected(config):
        return
    directory = Path(__file__).parent.resolve()
    selected = [Path(str(item.fspath)).resolve().is_relative_to(directory) for item in items]
    if any(selected) and not all(selected):
        raise pytest.UsageError("browser 시험은 다른 시험과 섞지 않고 browser 디렉터리만 지정해 실행하세요")


@dataclass(frozen=True)
class ViewerServer:
    base_url: str
    repo_id: str
    ref: str
    sha: str


@pytest.fixture(scope="session")
def browser(request):
    if not _explicitly_selected(request.config):
        pytest.skip("browser 디렉터리를 명시하여 선택 실행하세요")
    playwright = pytest.importorskip("playwright.sync_api", reason="browser 의존성 group이 필요합니다")
    with playwright.sync_playwright() as runtime:
        try:
            instance = runtime.chromium.launch(channel="chrome", headless=True)
        except playwright.Error as exc:
            if "not found" in str(exc) or "doesn't exist" in str(exc):
                pytest.skip("시스템 Google Chrome이 필요합니다 (브라우저 다운로드 없음)")
            raise
        try:
            yield instance
        finally:
            instance.close()


@pytest.fixture(scope="session")
def viewer_repository(tmp_path_factory):
    source = tmp_path_factory.mktemp("browser-git")
    subprocess.run(["git", "init", "-b", "main", str(source)], check=True, capture_output=True)
    (source / "motion").mkdir()
    (source / "doc.md").write_text(
        "# 브라우저 시험\n\n```synctx-html\nsrc: ./motion/index.html\ntitle: 모션\n```\n",
        encoding="utf-8",
    )
    (source / "motion/index.html").write_text(
        '<!DOCTYPE html><html><head><meta charset="utf-8"><title>모션</title></head><body>'
        '<button id="increment">증가</button><output id="counter">0</output>'
        '<a href="detail.html" target="pf">상세</a><iframe name="pf" title="상세 패널"></iframe>'
        '<script>window.executionMarker = "executed";'
        'document.querySelector("#increment").onclick = function () {'
        'document.querySelector("#counter").textContent = '
        'String(Number(document.querySelector("#counter").textContent) + 1);};</script>'
        '</body></html>',
        encoding="utf-8",
    )
    detail = source / "motion/detail.html"
    detail.write_text('<!DOCTYPE html><html><body><p id="snapshot">main snapshot</p>'
                      '<script>window.executionMarker = "executed";</script></body></html>', encoding="utf-8")
    subprocess.run(["git", "-C", str(source), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(source), "-c", "user.name=Test", "-c", "user.email=test@example.com",
                    "commit", "-m", "fixture"], check=True, capture_output=True)
    ref = "feature/browser"
    subprocess.run(["git", "-C", str(source), "checkout", "-b", ref], check=True, capture_output=True)
    detail.write_text(detail.read_text(encoding="utf-8").replace("main snapshot", "selected snapshot"), encoding="utf-8")
    subprocess.run(["git", "-C", str(source), "-c", "user.name=Test", "-c", "user.email=test@example.com",
                    "commit", "-am", "selected snapshot"], check=True, capture_output=True)
    sha = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"], check=True,
                         capture_output=True, text=True).stdout.strip()
    subprocess.run(["git", "-C", str(source), "checkout", "main"], check=True, capture_output=True)
    return source, ref, sha


@pytest.fixture(scope="session")
def viewer_server_factory(browser, viewer_repository, tmp_path_factory):
    import uvicorn

    standalone = importlib.import_module("standalone")
    source, ref, sha = viewer_repository

    @contextmanager
    def start(ttl_seconds=14400):
        directory = tmp_path_factory.mktemp("browser-viewer")
        with socket.socket() as listener, pytest.MonkeyPatch.context() as patch:
            listener.bind(("127.0.0.1", 0))
            base_url = f"http://127.0.0.1:{listener.getsockname()[1]}"
            repo_id = "browser-repo"
            path = directory / "config.yaml"
            path.write_text(yaml.safe_dump({
                "authz": {"enabled": False},
                "runtime": {"public_base_url": base_url},
                "viewer": {"asset_source": "bundled", "repo_cache_path": str(directory / "cache"),
                           "render_token_ttl_seconds": ttl_seconds},
                "repositories": [{"id": repo_id, "name": "Browser", "url": str(source),
                                  "auth": {"type": "none"},
                                  "html_embed": {"enabled": True, "render_files": True}}],
            }), encoding="utf-8")
            patch.setenv("SYNCTX_CONFIG_PATH", str(path))
            patch.setenv("SYNCTX_ENABLED_SERVICES", "viewer")
            for name in ("SYNCTX_RENDER_SECRET", "SYNCTX_RENDER_SECRET_FILE",
                         "SYNCTX_RENDER_SECRET_PREVIOUS", "SYNCTX_RENDER_SECRET_PREVIOUS_FILE"):
                patch.delenv(name, raising=False)
            viewer = standalone._load_viewer()
            application = standalone.create_app(refresh_interval=0, viewer=viewer)
            server = uvicorn.Server(uvicorn.Config(application, log_config=None, access_log=False))
            thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
            try:
                thread.start()
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    if not thread.is_alive():
                        pytest.fail("Viewer 서버가 시작 중 종료되었습니다")
                    try:
                        with urlopen(f"{base_url}/view/readyz", timeout=0.2) as response:
                            if response.status == 200:
                                break
                    except OSError:
                        pass
                    time.sleep(0.05)
                else:
                    pytest.fail("Viewer 서버가 준비되지 않았습니다")
                yield ViewerServer(base_url, repo_id, ref, sha)
            finally:
                server.should_exit = True
                thread.join(timeout=10)
                if thread.is_alive():
                    server.force_exit = True
                    thread.join(timeout=5)
                assert not thread.is_alive(), "Viewer 서버 스레드가 종료되지 않았습니다"

    with ExitStack() as stack:
        yield lambda **kwargs: stack.enter_context(start(**kwargs))


@pytest.fixture(scope="session")
def viewer_server(viewer_server_factory):
    return viewer_server_factory()


@pytest.fixture
def page(browser):
    context = browser.new_context(service_workers="block")
    external_requests = []

    def local_only(route):
        url = urlsplit(route.request.url)
        if url.scheme in ("http", "https") and url.hostname not in ("127.0.0.1", "localhost", "::1"):
            external_requests.append(route.request.url)
            route.abort()
        else:
            route.continue_()

    context.route("**/*", local_only)
    try:
        page = context.new_page()
        page.set_default_timeout(5000)
        yield page
    finally:
        context.close()
    assert not external_requests, f"외부 요청 시도: {external_requests}"

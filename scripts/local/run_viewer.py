"""Docker 없이 로컬에서 Viewer·Marp를 한 프로세스로 띄운다.

/view → Viewer, /marp → Marp, /cdn → 저장소의 cdn-data 를 Ingress와 같은 경로로 묶는다.
설정은 helm/synctx/values-local.yaml 의 synctxConfig.data(minikube와 같은 저장소 목록)에서
만들고, 로컬에 맞는 값(주소, 저장소 캐시 위치)만 바꾼다.

사용법: scripts/local/run-viewer.sh [--port 18000] [--config <.synctx.yaml>]
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import os
import shutil
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_VALUES = PROJECT_ROOT / "helm" / "synctx" / "values-local.yaml"
DEFAULT_SECRET = PROJECT_ROOT / "helm" / "synctx" / "values-local.secret.yaml"
DEFAULT_CACHE = Path.home() / ".cache" / "synctx"
MAC_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def build_local_config(values_file: Path, port: int, cache_dir: Path) -> dict:
    values = yaml.safe_load(values_file.read_text()) or {}
    data = (values.get("synctxConfig") or {}).get("data") or ""
    config = yaml.safe_load(data) or {}
    if not config.get("repositories"):
        raise SystemExit(f"{values_file}의 synctxConfig.data에 repositories가 없습니다. --config로 설정 파일을 지정하세요")

    base = f"http://127.0.0.1:{port}"
    config.setdefault("runtime", {})["public_base_url"] = base
    viewer = config.setdefault("viewer", {})
    viewer.setdefault("render_token_ttl_seconds", 14400)
    viewer["cdn_base_url"] = "/cdn"
    viewer["marp_service_url"] = f"{base}/marp"
    # 컨테이너 기본값(/data/...)은 macOS에서 쓸 수 없는 경로라 사용자 캐시로 옮긴다
    viewer["repo_cache_path"] = str(cache_dir / "repos")
    marp = config.setdefault("marp", {})
    marp["worktree_path"] = str(cache_dir / "marp-publish")
    # Marp 출판은 첫 저장소의 읽기 인증을 그대로 쓰므로 그 토큰에 쓰기 권한이 있으면 공유 저장소에 push된다
    marp["publish_repo"] = ""
    return config


def read_token(secret_file: Path) -> str:
    token = os.getenv("GIT_READ_TOKEN", "")
    if token or not secret_file.is_file():
        return token
    secrets = yaml.safe_load(secret_file.read_text()) or {}
    return ((secrets.get("git") or {}).get("secrets") or {}).get("readToken") or ""


def find_chrome() -> str:
    for candidate in (os.getenv("CHROME_PATH", ""), MAC_CHROME, shutil.which("chromium") or "", shutil.which("google-chrome") or ""):
        if candidate and Path(candidate).exists():
            return candidate
    return ""


def enabled_services(marp_ready: bool) -> str:
    return "viewer,cdn,marp" if marp_ready else "viewer,cdn"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def create_app(project_root: Path = PROJECT_ROOT):
    """SYNCTX_CONFIG_PATH·SYNCTX_ENABLED_SERVICES가 설정된 뒤에 불러야 한다(앱이 import 시점에 설정을 읽는다)."""
    from starlette.applications import Starlette
    from starlette.responses import RedirectResponse
    from starlette.routing import Mount, Route
    from starlette.staticfiles import StaticFiles

    # Viewer는 renderer·embed_view·meeting 같은 형제 모듈을 이름으로 import한다.
    # 두 서비스 모두 app.py라서 sys.modules의 "app"과 겹치지 않는 이름으로 불러온다.
    viewer_src = str(project_root / "services" / "viewer" / "src")
    if viewer_src not in sys.path:
        sys.path.append(viewer_src)
    viewer = _load_module("synctx_local_viewer_app", project_root / "services" / "viewer" / "src" / "app.py")
    marp = _load_module("synctx_local_marp_app", project_root / "services" / "marp" / "src" / "app.py")

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        # Mount는 하위 앱의 lifespan을 실행하지 않으므로 직접 묶는다(Viewer 시작 검사·저장소 동기화)
        async with contextlib.AsyncExitStack() as stack:
            await stack.enter_async_context(viewer.app.router.lifespan_context(viewer.app))
            await stack.enter_async_context(marp.app.router.lifespan_context(marp.app))
            yield

    return Starlette(
        routes=[
            Route("/", lambda request: RedirectResponse("/view/")),
            Mount("/view", app=viewer.app),
            Mount("/marp", app=marp.app),
            Mount("/cdn", app=StaticFiles(directory=project_root / "cdn-data")),
        ],
        lifespan=lifespan,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Docker 없이 로컬에서 synctx Viewer를 실행한다")
    parser.add_argument("--port", type=int, default=18000)
    parser.add_argument("--config", type=Path, help=".synctx.yaml 경로(없으면 values-local.yaml에서 만든다)")
    parser.add_argument("--values", type=Path, default=DEFAULT_VALUES)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    args = parser.parse_args()

    args.cache_dir.mkdir(parents=True, exist_ok=True)
    if args.config:
        config_path = args.config.resolve()
    else:
        config_path = args.cache_dir / "synctx.local.yaml"
        config = build_local_config(args.values, args.port, args.cache_dir)
        config_path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False))
    os.environ["SYNCTX_CONFIG_PATH"] = str(config_path)

    token = read_token(DEFAULT_SECRET)
    if token:
        os.environ["GIT_READ_TOKEN"] = token
    else:
        print("⚠ GIT_READ_TOKEN이 없어 공개 저장소만 받을 수 있습니다", file=sys.stderr)

    chrome = find_chrome()
    missing = [tool for tool in ("marp", "mmdc") if not shutil.which(tool)] + ([] if chrome else ["Chrome/Chromium"])
    if missing:
        print(
            f"⚠ {', '.join(missing)}이(가) 없어 Marp를 끄고 실행합니다. "
            "설치: npm install -g @marp-team/marp-cli @mermaid-js/mermaid-cli (Chrome은 별도 설치)",
            file=sys.stderr,
        )
    else:
        os.environ["CHROME_PATH"] = chrome
    os.environ["SYNCTX_ENABLED_SERVICES"] = enabled_services(marp_ready=not missing)

    import uvicorn

    print(f"synctx Viewer: http://127.0.0.1:{args.port}/view/  (설정: {config_path})")
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, log_level="info", log_config=None)


if __name__ == "__main__":
    main()

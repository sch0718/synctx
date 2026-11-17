import os
import asyncio
import difflib
import functools
import hmac
import json
import html as html_lib
import logging
import subprocess
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from urllib.parse import quote, unquote, urlsplit

from fastapi import FastAPI, HTTPException, Request, Depends, Header, Query
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from synctx_common.config import HtmlEmbedConfig, load_config
from synctx_common.frontmatter import parse_frontmatter
from synctx_common.git_manager import GitRepoManager
from synctx_common.html_bundle import BundleError, bundle, repo_reader, resolved_reader
from synctx_common.logging import configure_logging
from synctx_common.observability import (
    html_render_total, register_observability_routes,
    viewer_jitsi_tokens_issued_total, viewer_jitsi_token_failures_total,
)
from synctx_common.render_token import RenderTokenError, issue_render_token, load_render_keys, verify_render_token
from renderer import NO_TITLE_FALLBACK, render_markdown, rewrite_links, THEME_CSS, THEME_HLJS, THEME_MERMAID

import embed_view
import evidence
import jitsi
import meeting as meeting_mode
import meeting_card
import meeting_index
import search as search_mod
import viewer_ui as ui

__version__ = "0.8.3"
configure_logging(service_name="viewer")
VIEW_PREFIX = "/view"

config = load_config()
render_key, render_previous_key = load_render_keys()
jitsi_keys = jitsi.load_keys(config.collab.jitsi)
logger = logging.getLogger(__name__)


_startup_sync: asyncio.Task | None = None


async def _sync_repos_on_startup():
    for repo in config.repositories:
        try:
            manager = get_repo_manager(repo.id)
            await manager.ensure()
        except Exception as e:
            print(f"Failed to initialize repo {repo.id}: {e}")


def _startup_sync_finished() -> bool:
    """처음 clone·fetch가 끝나야 트래픽을 받는다. 실패한 저장소는 요청 때 오류로 알린다."""
    return _startup_sync is None or _startup_sync.done()


def _enabled_services() -> frozenset[str] | None:
    """배포 도구가 넘긴 켜진 서비스 목록. 값이 없으면 None이고 모두 켜진 것으로 본다."""
    raw = os.getenv("SYNCTX_ENABLED_SERVICES", "")
    names = {name.strip().lower() for name in raw.split(",") if name.strip()}
    return frozenset(names) if names else None


def _service_enabled(name: str) -> bool:
    enabled = _enabled_services()
    return enabled is None or name in enabled


def _check_service_dependencies(cfg) -> None:
    cdn_url = cfg.viewer.cdn_base_url
    if cfg.viewer.asset_source == "cdn" and not _service_enabled("cdn") and cdn_url.startswith("/") and not cdn_url.startswith("//"):
        raise RuntimeError(
            f"CDN 서비스가 꺼져 있는데 viewer.cdn_base_url이 상대 경로입니다({cdn_url}). "
            "내부 CDN의 절대 주소를 지정하거나 CDN을 켜세요."
        )
    for repo in cfg.repositories:
        if repo.id in RESERVED_PATHS:
            logger.warning("repository_id_reserved repo_id=%s: 예약된 경로와 같아 /view/%s 주소로 열 수 없습니다",
                           repo.id, repo.id)


@asynccontextmanager
async def lifespan(application: FastAPI):
    # 저장소를 다 받을 때까지 기다리면 startup probe 시간을 넘기므로 백그라운드로 돌린다
    global _startup_sync, jitsi_keys
    _check_service_dependencies(config)
    jitsi_keys = jitsi.load_keys(config.collab.jitsi)
    _startup_sync = asyncio.create_task(_sync_repos_on_startup())
    try:
        yield
    finally:
        _startup_sync.cancel()
        with suppress(asyncio.CancelledError):
            await _startup_sync


app = FastAPI(title="synctx-viewer", description="Viewer Service for synctx", version=__version__, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")
register_observability_routes(app, version=__version__, service_name="viewer", ready_check=_startup_sync_finished)


@app.middleware("http")
async def csp_middleware(request: Request, call_next):
    static = request.scope["path"][len(request.scope.get("root_path", "")):].startswith("/static/")
    try:
        response = await call_next(request)
    except Exception:
        if getattr(request.scope.get("route"), "endpoint", None) not in (render_html, open_file):
            raise
        logger.exception("html_render_failed")
        response = JSONResponse(status_code=500, content={"detail": "HTML을 불러오지 못했습니다"})
    if getattr(request.scope.get("route"), "endpoint", None) is meeting_jitsi:
        response.headers["Cache-Control"] = "no-store"
    if static:
        response.headers["Cache-Control"] = "no-cache"
    if getattr(request.scope.get("route"), "endpoint", None) in (render_html, open_file):
        response.headers.update({
            "Content-Security-Policy": "sandbox allow-scripts",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        })
        if "X-Frame-Options" in response.headers:
            del response.headers["X-Frame-Options"]
        return response
    render_screen = response.headers.get("X-Synctx-Html-Render") == "true"
    if "X-Synctx-Html-Render" in response.headers:
        del response.headers["X-Synctx-Html-Render"]
    if "text/html" in response.headers.get("content-type", ""):
        cdn_url = config.viewer.cdn_base_url if config.viewer.asset_source == "cdn" else ""
        cdn_host = ""
        if cdn_url.startswith(("http://", "https://")):
            from urllib.parse import urlparse
            parsed = urlparse(cdn_url)
            cdn_host = f" {parsed.scheme}://{parsed.netloc}"
        elif cdn_url.startswith("//"):
            cdn_host = f" {cdn_url}"
            
        embed_hosts = "".join(f" {origin}" for origin in config.html_embed.allowed_origins)
        frame_src = "'self'" if render_screen else "'none'"
        jitsi_origin = ""
        if config.collab.jitsi.enabled:
            jitsi_origin = " " + config.collab.jitsi.public_url
            frame_src = ("'self' " if render_screen else "") + config.collab.jitsi.public_url
            origin = config.collab.jitsi.public_url
            response.headers["Permissions-Policy"] = ", ".join(
                f'{feature}=(self "{origin}")' for feature in ("camera", "microphone", "display-capture")
            )
        csp = (
            f"default-src 'self'; "
            f"script-src 'self' 'unsafe-inline'{cdn_host}{embed_hosts}{jitsi_origin}; "
            f"style-src 'self' 'unsafe-inline'{cdn_host}{embed_hosts}; "
            f"img-src 'self' data:{cdn_host}{embed_hosts}; "
            f"font-src 'self' data:{cdn_host}{embed_hosts}; "
            f"connect-src 'self'{cdn_host}{embed_hosts}; "
            f"frame-src {frame_src}; "
            f"object-src 'none'"
        )
        response.headers["Content-Security-Policy"] = csp
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
    return response


repo_managers = {}


def get_repo_manager(repo_id: str) -> GitRepoManager:
    if repo_id in repo_managers:
        return repo_managers[repo_id]
    for repo in config.repositories:
        if repo.id == repo_id:
            base_dir = config.viewer.repo_cache_path
            manager = GitRepoManager(repo, base_dir)
            repo_managers[repo_id] = manager
            return manager
    raise HTTPException(status_code=404, detail=f"Repository not found: {repo_id}")


def _html_embed_policy(repo_id: str) -> HtmlEmbedConfig:
    for repo in config.repositories:
        if repo.id == repo_id:
            return repo.html_embed
    return HtmlEmbedConfig()


def _render_download_html(manager: GitRepoManager, repo_id: str, path: str, ref: str | None, filename_base: str) -> str:
    text_content = manager.read_file_text(path, ref=ref)
    prepared = embed_view.prepare(text_content)
    rendered = render_markdown(prepared.text)
    html = _sanitize_html(rendered.get("html", ""))
    public = config.runtime.public_base_url.rstrip("/")
    html = embed_view.fill(
        html, prepared,
        md_path=path,
        policy=_html_embed_policy(repo_id),
        read=repo_reader(manager, ref),
        repo_id=repo_id,
        source_href=lambda src: f"{public}{VIEW_PREFIX}/{repo_id}/{quote(src)}",
        download=True,
    )
    return f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>{filename_base}</title></head><body>{html}</body></html>"


def _prepare_pdf_markdown(manager: GitRepoManager, repo_id: str, path: str, ref: str | None) -> str:
    public = config.runtime.public_base_url.rstrip("/")
    return embed_view.strip_for_pdf(
        manager.read_file_text(path, ref=ref),
        f"{public}{VIEW_PREFIX}/{repo_id}/{quote(path)}",
    )


def verify_viewer_access(x_forwarded_groups: str = Header(default="")):
    if not config.authz.enabled:
        return
    groups = [g.strip() for g in x_forwarded_groups.split(",") if g.strip()]
    allowed_roles = config.authz.role_mappings.viewer + config.authz.role_mappings.platform_admin
    if not any(role in groups for role in allowed_roles):
        raise HTTPException(status_code=403, detail="Forbidden: viewer role required")


def verify_viewer_or_service_access(authorization: str = Header(default=""),
                                    x_forwarded_groups: str = Header(default="")):
    """다른 서비스(Collab Gateway)가 쓰는 Bearer 서비스 토큰을 먼저 보고, 아니면 일반 Viewer 권한을 본다."""
    expected = os.getenv(config.collab.gateway.viewer_service_token_env, "")
    scheme, _, presented = authorization.partition(" ")
    if expected and scheme == "Bearer" and presented and hmac.compare_digest(presented.encode(), expected.encode()):
        return True
    verify_viewer_access(x_forwarded_groups)
    return False


def _view_url(path=""):
    return f"{VIEW_PREFIX}/{path}" if path else VIEW_PREFIX


# ── API Routes ──

@app.get("/api/repos", dependencies=[Depends(verify_viewer_access)])
def list_repos():
    return {
        "repositories": [
            {"id": r.id, "name": r.name, "url": r.url}
            for r in config.repositories
        ]
    }


@app.get("/api/repos/{repo_id}/branches", dependencies=[Depends(verify_viewer_access)])
def list_branches(repo_id: str):
    manager = get_repo_manager(repo_id)
    try:
        branches = manager.list_branches()
        return {"branches": branches, "default": manager.profile.default_ref}
    except Exception as e:
        logger.warning("list_branches_failed repo_id=%s error=%s", repo_id, type(e).__name__)
        raise HTTPException(status_code=400, detail="Failed to list branches")


@app.get("/api/repos/{repo_id}/head", dependencies=[Depends(verify_viewer_access)])
def head_commit(repo_id: str, ref: str | None = None):
    manager = get_repo_manager(repo_id)
    try:
        sha = manager.resolve_commit_sha(ref or manager.profile.default_ref)
    except Exception as e:
        logger.warning("head_commit_failed repo_id=%s error=%s", repo_id, type(e).__name__)
        raise HTTPException(status_code=404, detail="Ref not found")
    return JSONResponse({"sha": sha}, headers={"Cache-Control": "no-store"})


@app.get("/api/repos/{repo_id}/tree", dependencies=[Depends(verify_viewer_access)])
def get_tree(repo_id: str, path: str = "", ref: str | None = None):
    manager = get_repo_manager(repo_id)
    try:
        entries = manager.list_tree(path, ref=ref)
        result = []
        for e in entries:
            entry = e.__dict__.copy()
            if e.type == "file":
                entry["viewable"] = is_viewable(e.path)
                entry["icon_type"] = _file_icon_type(e.path)
            else:
                entry["viewable"] = True
                entry["icon_type"] = "dir"
            result.append(entry)
        return {"path": path, "entries": result}
    except Exception as e:
        logger.warning("list_tree_failed repo_id=%s error=%s", repo_id, type(e).__name__)
        raise HTTPException(status_code=400, detail="Failed to list files")


@app.get("/api/repos/{repo_id}/files", dependencies=[Depends(verify_viewer_access)])
def list_files(repo_id: str, ref: str | None = None):
    manager = get_repo_manager(repo_id)
    effective_ref = ref if ref and ref != manager.profile.default_ref else None
    try:
        paths = manager.file_paths(ref=effective_ref)
    except Exception:
        logger.warning("파일 목록을 읽지 못했습니다: %s", repo_id, exc_info=True)
        raise HTTPException(status_code=502, detail="파일 목록을 불러오지 못했습니다")
    return {"files": sorted(p for p in paths if is_viewable(p))}


def _attachment_disposition(filename: str) -> str:
    """RFC 6266/5987: 구형 클라이언트용 ASCII filename과 원래 이름을 담은 filename*를 함께 보낸다."""
    fallback = "".join(c if " " <= c < "\x7f" else "_" for c in filename)
    fallback = fallback.replace("\\", "\\\\").replace('"', '\\"')
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"


@app.get("/api/repos/{repo_id}/download", dependencies=[Depends(verify_viewer_access)])
async def download_file(repo_id: str, path: str, format: str = "raw", ref: str | None = None,
                        x_forwarded_groups: str = Header(default="")):
    manager = get_repo_manager(repo_id)
    try:
        filename_base = PurePosixPath(path).stem or "download"

        if format == "raw":
            content = manager.read_file(path, ref=ref)
            filename = os.path.basename(path.strip("/")) or "download"
            return Response(
                content=content,
                media_type="application/octet-stream",
                headers={"Content-Disposition": _attachment_disposition(filename)},
            )

        if format == "pdf" and not _service_enabled("marp"):
            raise HTTPException(status_code=503, detail="Marp 서비스가 꺼져 있어 PDF로 변환할 수 없습니다")

        if format in ("html", "pdf"):
            try:
                blob_size = manager.file_size(path, ref=ref)
            except Exception:
                blob_size = 0
            if blob_size > config.viewer.max_render_size_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"File too large for conversion ({blob_size} bytes, max {config.viewer.max_render_size_bytes})",
                )

        if format == "html":
            loop = asyncio.get_event_loop()
            html_doc = await loop.run_in_executor(
                None, functools.partial(_render_download_html, manager, repo_id, path, ref, filename_base)
            )
            return Response(
                content=html_doc.encode("utf-8"),
                media_type="text/html",
                headers={"Content-Disposition": _attachment_disposition(f"{filename_base}.html")},
            )

        if format == "pdf":
            loop = asyncio.get_event_loop()
            text_content = await loop.run_in_executor(
                None, functools.partial(_prepare_pdf_markdown, manager, repo_id, path, ref)
            )
            import httpx
            marp_url = config.viewer.marp_service_url.rstrip("/")
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    # 서비스 간 호출은 Ingress를 거치지 않으므로 Marp 권한 검사에 쓸 그룹 헤더를 넘긴다
                    resp = await client.post(
                        f"{marp_url}/api/marp/convert",
                        json={"markdown": text_content},
                        headers={"X-Forwarded-Groups": x_forwarded_groups},
                    )
            except httpx.HTTPError:
                logger.warning("marp_convert_failed repo_id=%s path=%s", repo_id, path, exc_info=True)
                raise HTTPException(status_code=502, detail="PDF conversion failed")
            if resp.status_code == 403:
                raise HTTPException(status_code=403, detail="PDF conversion forbidden")
            if resp.status_code != 200:
                raise HTTPException(status_code=502, detail="PDF conversion failed")
            return Response(
                content=resp.content,
                media_type="application/pdf",
                headers={"Content-Disposition": _attachment_disposition(f"{filename_base}.pdf")},
            )

        raise HTTPException(status_code=400, detail=f"Unsupported format: {format}. Use raw, html, or pdf.")

    except HTTPException:
        raise
    except Exception as e:
        if isinstance(e, (subprocess.CalledProcessError, ValueError)) and not isinstance(e, UnicodeEncodeError):
            logger.warning("download_not_found repo_id=%s error=%s", repo_id, type(e).__name__)
            raise HTTPException(status_code=404, detail="File not found")
        logger.exception("download_failed repo_id=%s path=%s format=%s", repo_id, path, format)
        raise HTTPException(status_code=500, detail="Download failed")


# 본문이 <img>로 참조하는 그림은 맞는 형식으로 준다. 브라우저는 SVG를 image/svg+xml일 때만 그린다.
RAW_IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
                   ".webp": "image/webp", ".svg": "image/svg+xml"}
# 저장소의 SVG는 스크립트를 담을 수 있으므로, 주소를 직접 열어도 실행되지 않게 막는다(<img>로는 원래 실행되지 않음).
RAW_SVG_CSP = "default-src 'none'; style-src 'unsafe-inline'; sandbox"


@app.get("/raw/{repo_id}/{path:path}", dependencies=[Depends(verify_viewer_access)])
def raw_file(repo_id: str, path: str, ref: str | None = None):
    manager = get_repo_manager(repo_id)
    suffix = PurePosixPath(path).suffix.lower()
    media_type = RAW_IMAGE_TYPES.get(suffix, "application/octet-stream")
    headers = {"X-Content-Type-Options": "nosniff"} if suffix in RAW_IMAGE_TYPES else {}
    if suffix == ".svg":
        headers["Content-Security-Policy"] = RAW_SVG_CSP
    try:
        return Response(content=manager.read_file(path, ref=ref), media_type=media_type, headers=headers)
    except Exception as e:
        logger.warning("raw_file_failed repo_id=%s error=%s", repo_id, type(e).__name__)
        raise HTTPException(status_code=404, detail="File not found")


def _verify_service_token(request: Request):
    expected = os.getenv("SYNCTX_SERVICE_TOKEN", "")
    if not expected:
        return
    token = request.headers.get("X-Service-Token", "")
    if token != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing service token")


@app.post("/api/repos/{repo_id}/webhook", dependencies=[Depends(verify_viewer_access)])
async def repo_webhook(repo_id: str, request: Request):
    _verify_service_token(request)
    manager = get_repo_manager(repo_id)
    payload = await request.json()
    source = payload.get("after") or payload.get("checkout_sha") or "webhook"
    try:
        head_sha = await manager.fetch(source=f"webhook:{source}")
        return {"status": "ok", "repo_id": repo_id, "head_sha": head_sha}
    except Exception as e:
        logger.warning("webhook_refresh_failed repo_id=%s error=%s", repo_id, type(e).__name__)
        raise HTTPException(status_code=502, detail="Repository refresh failed")


# ── File Type Detection ──

CODE_LANGUAGE_MAP = {
    ".py": "python", ".pyi": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript",
    ".go": "go", ".java": "java", ".kt": "kotlin", ".kts": "kotlin",
    ".rs": "rust", ".rb": "ruby", ".php": "php", ".cs": "csharp",
    ".swift": "swift", ".scala": "scala",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp",
    ".m": "objectivec", ".mm": "objectivec",
    ".sh": "bash", ".bash": "bash", ".zsh": "bash",
    ".ps1": "powershell", ".bat": "dos", ".cmd": "dos",
    ".sql": "sql",
    ".yaml": "yaml", ".yml": "yaml",
    ".json": "json", ".jsonc": "json",
    ".toml": "toml", ".ini": "ini", ".cfg": "ini", ".conf": "ini",
    ".xml": "xml", ".html": "html", ".htm": "html",
    ".css": "css", ".scss": "scss", ".less": "less",
    ".vue": "xml", ".lua": "lua", ".r": "r", ".pl": "perl",
    ".dart": "dart", ".groovy": "groovy", ".gradle": "groovy",
    ".tf": "hcl", ".hcl": "hcl", ".dockerfile": "dockerfile",
    ".env": "bash", ".txt": "plaintext", ".log": "plaintext",
    ".csv": "plaintext", ".tsv": "plaintext",
    ".properties": "ini", ".makefile": "makefile", ".mk": "makefile",
}

CODE_FILENAME_MAP = {
    "dockerfile": "dockerfile", "makefile": "makefile", "gnumakefile": "makefile",
    "jenkinsfile": "groovy", "vagrantfile": "ruby", "gemfile": "ruby",
    "rakefile": "ruby", "podfile": "ruby",
    ".gitignore": "bash", ".gitattributes": "bash", ".dockerignore": "bash",
    ".editorconfig": "ini",
}

MARKDOWN_EXTENSIONS = {".md", ".markdown"}


def get_code_language(path: str):
    pure = PurePosixPath(path)
    name = pure.name.lower()
    if name in CODE_FILENAME_MAP:
        return CODE_FILENAME_MAP[name]
    return CODE_LANGUAGE_MAP.get(pure.suffix.lower())


def is_viewable(path: str) -> bool:
    suffix = PurePosixPath(path).suffix.lower()
    return suffix in MARKDOWN_EXTENSIONS or get_code_language(path) is not None


def _file_icon_type(path: str) -> str:
    suffix = PurePosixPath(path).suffix.lower()
    if suffix in MARKDOWN_EXTENSIONS:
        return "markdown"
    if get_code_language(path) is not None:
        return "code"
    return "binary"


def _file_icon_svg(path: str) -> str:
    t = _file_icon_type(path)
    if t == "markdown":
        return '<svg class="icon-md" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>'
    if t == "code":
        return '<svg class="icon-code" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/><path d="m10 13-2 2 2 2"/><path d="m14 13 2 2-2 2"/></svg>'
    return '<svg class="icon-binary" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>'


def _wrap_code_as_markdown(path: str, content: str, language: str) -> str:
    filename = PurePosixPath(path).name
    fence = "```"
    while fence in content:
        fence += "`"
    return f"# {filename}\n\n{fence}{language}\n{content}\n{fence}\n"


# ── View Helpers ──

def generate_toc_html(toc_tokens):
    html = ""
    for token in toc_tokens:
        html += f'<li><a href="#{token["id"]}">{token["name"]}</a>'
        if token.get("children"):
            html += "<ul>" + generate_toc_html(token["children"]) + "</ul>"
        html += "</li>"
    return html


DIR_ICON_SVG = '<svg class="icon-dir" viewBox="0 0 24 24" fill="currentColor"><path d="M10 4H4a2 2 0 00-2 2v12a2 2 0 002 2h16a2 2 0 002-2V8a2 2 0 00-2-2h-8l-2-2z"/></svg>'


def _build_path_trail(repo_id, path, ref, separator):
    ref_param = f"&ref={quote(ref, safe='')}" if ref else ""
    parts = [p for p in path.split("/") if p]
    items = []
    for i, name in enumerate(parts[:-1]):
        folder = "/".join(parts[: i + 1])
        href = f"{VIEW_PREFIX}/{quote(repo_id, safe='')}?path={quote(folder)}{ref_param}"
        items.append(f'<a href="{html_lib.escape(href)}">{html_lib.escape(name)}</a>')
    if parts:
        items.append(f'<span class="current">{html_lib.escape(parts[-1])}</span>')
    return separator.join(items)


def _active_branches(manager, ref: str | None) -> list[str]:
    """화면을 그릴 때는 원격을 조회하지 않고 현재 브랜치만 담는다. 전체 목록은 화면이 뜬 뒤 viewer.js가 채운다."""
    return [ref or manager.profile.default_ref]


def _shell_header(repo_id: str | None, ref: str | None, theme: str) -> str:
    default_ref = status = branches = None
    if repo_id:
        manager = get_repo_manager(repo_id)
        default_ref = manager.profile.default_ref
        status = manager.status
        branches = _active_branches(manager, ref)
    return ui.render_header(
        view_prefix=VIEW_PREFIX, repos=config.repositories, theme=theme, active_repo=repo_id,
        branches=branches, active_ref=ref, default_ref=default_ref, repo_status=status,
        meetings_link=bool(_collab_repos()),
    )


def _nav_controls(repo_id: str | None, ref: str | None, theme: str) -> str:
    default_ref = branches = None
    if repo_id:
        manager = get_repo_manager(repo_id)
        default_ref = manager.profile.default_ref
        branches = _active_branches(manager, ref)
    return ui.render_nav_controls(
        repos=config.repositories, theme=theme, active_repo=repo_id, branches=branches,
        active_ref=ref, default_ref=default_ref,
    )


def _document_title(rendered_title, front_matter: dict, path: str) -> str:
    """H1이 없어 렌더러가 영어 대체 제목을 돌려주면 회의 제목, 그것도 없으면 파일 이름을 쓴다."""
    if rendered_title and rendered_title != NO_TITLE_FALLBACK:
        return rendered_title
    synctx_meta = front_matter.get("synctx") if isinstance(front_matter, dict) else None
    meeting = synctx_meta.get("meeting") if isinstance(synctx_meta, dict) else None
    meeting_title = meeting.get("title") if isinstance(meeting, dict) else None
    if isinstance(meeting_title, str) and meeting_title.strip():
        return meeting_title.strip()
    return PurePosixPath(path).name


def _commit_line(manager, path: str, ref: str | None) -> str:
    try:
        commit = manager.last_commit(path, ref=ref)
    except Exception:
        logger.warning("마지막 커밋을 읽지 못했습니다: %s", path or "(저장소)", exc_info=True)
        return ""
    when = ui.relative_time_ko(commit.authored_at)
    return (f'<span>마지막 커밋 {ui.esc(commit.author)}</span>'
            f'<time datetime="{ui.esc(commit.authored_at)}" title="{ui.esc(commit.authored_at)}">{ui.esc(when)}</time>'
            f'<code class="sha">{ui.esc(str(commit.sha)[:7])}</code>')


RECENT_SECTION = ('<section class="recent" aria-label="최근 본 문서"><h2 class="nav-title">최근 본 문서</h2>'
                  '<ul class="recent-list" data-recent></ul></section>')
TREE_FILTER = ('<label class="tree-filter"><span class="visually-hidden">파일 이름으로 좁히기</span>'
               '<input type="search" data-tree-filter placeholder="파일 이름으로 좁히기" aria-label="파일 이름으로 좁히기"></label>')


def _build_tree_html(repo_id, path, ref=None, link_ref=None, current=None):
    """path 폴더의 트리. current 파일이 주어지면 그 파일까지 경로상의 폴더를 펼치고 파일을 선택 상태로 둔다.

    ref는 목록을 읽을 브랜치, link_ref는 파일 링크에 붙일 ref(없으면 ref)다.
    """
    try:
        manager = get_repo_manager(repo_id)
        html = _tree_level_html(manager, repo_id, path, ref, link_ref if link_ref is not None else ref, current)
    except Exception:
        logger.warning("파일 트리를 읽지 못했습니다: %s", repo_id, exc_info=True)
        return '<div class="empty-state"><p>파일 트리를 불러오지 못했습니다.</p></div>'
    return html or '<div class="tree-note">빈 저장소</div>'


def _tree_level_html(manager, repo_id, path, ref, link_ref, current):
    entries = manager.list_tree(path, ref=ref)
    ref_param = f"?ref={quote(link_ref, safe='')}" if link_ref else ""
    dirs = sorted([e for e in entries if e.type == "dir"], key=lambda e: e.name)
    files = sorted([e for e in entries if e.type == "file"], key=lambda e: e.name)

    html = ""
    for e in dirs:
        name = html_lib.escape(e.name)
        data_path = html_lib.escape(e.path)
        if current and current.startswith(e.path + "/"):
            children = _tree_level_html(manager, repo_id, e.path, ref, link_ref, current)
            children = children or '<div class="tree-note">비어 있음</div>'
            toggle_class, children_html = "tree-toggle open", f'<div class="tree-children" data-path="{data_path}" data-loaded="1">{children}</div>'
        else:
            toggle_class, children_html = "tree-toggle", f'<div class="tree-children collapsed" data-path="{data_path}"></div>'
        expanded = "true" if toggle_class.endswith("open") else "false"
        html += f'''<button type="button" class="tree-entry tree-dir" aria-expanded="{expanded}">
            <svg class="{toggle_class}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="m9 18 6-6-6-6"/></svg>
            {DIR_ICON_SVG}
            <span class="name">{name}/</span>
        </button>
        {children_html}'''
    for e in files:
        icon = _file_icon_svg(e.path)
        name = html_lib.escape(e.name)
        if is_viewable(e.path):
            file_url = html_lib.escape(_view_url(f"{repo_id}/{quote(e.path)}") + ref_param)
            is_current = e.path == current
            entry_class = "tree-entry current" if is_current else "tree-entry"
            current_attr = ' aria-current="page"' if is_current else ""
            html += f'''<div class="{entry_class}">
            <svg style="width:18px" viewBox="0 0 24 24"></svg>
            {icon}
            <span class="name"><a href="{file_url}"{current_attr}>{name}</a></span>
        </div>'''
        else:
            html += f'''<div class="tree-entry disabled">
            <svg style="width:18px" viewBox="0 0 24 24"></svg>
            {icon}
            <span class="name">{name}</span>
        </div>'''
    return html


# ── Search ──

SEARCH_PATHSPECS = search_mod.viewable_pathspecs(MARKDOWN_EXTENSIONS, CODE_LANGUAGE_MAP, CODE_FILENAME_MAP)
SEARCH_QUERY_ERROR = "검색어는 두 글자 이상, 100글자 이하로 한 줄만 입력하세요"


def _searchable_path(path: str) -> bool:
    # 문서 주소 규칙이 제어 문자를 받지 않고, 파일 트리가 숨기는 점 경로는 검색에서도 뺀다
    if any(ord(char) < 32 or ord(char) == 127 for char in path):
        return False
    if any(part.startswith(".") for part in path.split("/")):
        return False
    return is_viewable(path)


async def _run_search(query: str, repo_id: str | None, ref: str | None, collab_only: bool = False) -> list[dict]:
    repos = _collab_repos() if collab_only else list(config.repositories)
    if repo_id:
        repos = [repo for repo in repos if repo.id == repo_id]
    managers = [(repo.id, repo.name, get_repo_manager(repo.id)) for repo in repos]
    effective_ref = None
    if repo_id and managers and ref and ref != managers[0][2].profile.default_ref:
        effective_ref = ref
    return await search_mod.search_repositories(managers, query, effective_ref, SEARCH_PATHSPECS, _searchable_path)


@app.get("/api/search")
async def search_documents(q: str | None = None, repo: str | None = None, ref: str | None = None,
                           collab_only: bool = False, service_access: bool = Depends(verify_viewer_or_service_access)):
    query = search_mod.normalize_query(q)
    if query is None:
        raise HTTPException(status_code=400, detail=SEARCH_QUERY_ERROR)
    if service_access and ref is not None:
        raise HTTPException(status_code=400, detail="서비스 토큰 검색은 기본 브랜치만 허용합니다")
    repo = repo or None
    ref = ref or None
    if ref and not repo:
        raise HTTPException(status_code=400, detail="ref는 저장소를 지정할 때만 쓸 수 있습니다")
    if repo and not _repo_known(repo):
        raise HTTPException(status_code=404, detail=f"Repository not found: {repo}")
    if service_access:
        _require_collab_repo(repo)
    results = await _run_search(query, repo, ref, collab_only or service_access)
    if service_access:
        results = [{**result, "matches": [{"path": match["path"]} for match in result["matches"]]}
                   for result in results]
    return {"query": query, "repos": results}


# ── View Routes ──

RESERVED_PATHS = {"api", "raw", "render", "open", "static", "search", "meetings", "healthz", "readyz", "version", "metrics", "docs", "openapi.json", "redoc"}


def _repo_known(repo_id: str) -> bool:
    return any(r.id == repo_id for r in config.repositories)


def _html_error(status_code: int, kind: str, *, repo_id=None, path=None, ref=None,
                suggestions=(), download_href=None, theme="github-light") -> HTMLResponse:
    repo = repo_id if repo_id and _repo_known(repo_id) else None
    try:
        header = _shell_header(repo, ref if repo else None, theme)
        controls = _nav_controls(repo, ref if repo else None, theme)
    except Exception:
        logger.warning("오류 화면 머리를 만들지 못했습니다", exc_info=True)
        repo = None
        header = _shell_header(None, None, theme)
        controls = _nav_controls(None, None, theme)
    body = ui.render_mobile_nav(controls=controls) + ui.error_body(
        kind, view_prefix=VIEW_PREFIX, repo_id=repo, path=path, ref=ref,
        suggestions=suggestions, download_href=download_href)
    html = ui.page(view_prefix=VIEW_PREFIX, title=ui.ERROR_COPY[kind][0], theme=theme, header=header,
                   body=body, repo_id=repo or "", ref=(ref or "") if repo else "", page_class="page-error")
    return HTMLResponse(content=html, status_code=status_code)


def _file_error_page(repo_id: str, path: str, ref: str | None, status_code: int, theme: str) -> HTMLResponse:
    query = f"?path={quote(path)}" + (f"&ref={quote(ref, safe='')}" if ref else "")
    download = f"{VIEW_PREFIX}/api/repos/{quote(repo_id, safe='')}/download{query}"
    if status_code == 413:
        return _html_error(413, "too_large", repo_id=repo_id, path=path, ref=ref, download_href=download, theme=theme)
    if status_code == 415:
        return _html_error(415, "unsupported", repo_id=repo_id, path=path, ref=ref, download_href=download, theme=theme)
    manager = get_repo_manager(repo_id)
    effective_ref = ref if ref and ref != manager.profile.default_ref else None
    try:
        paths = set(manager.file_paths(ref=effective_ref))
    except Exception:
        # 목록 조회 실패는 저장소 장애다. 파일이 없다고 안내하지 않는다
        logger.warning("파일 목록을 읽지 못했습니다: %s", repo_id, exc_info=True)
        return _html_error(502, "failed", repo_id=repo_id, path=path, ref=ref, theme=theme)
    if path not in paths:
        candidates = sorted(p for p in paths if is_viewable(p))
        suggestions = difflib.get_close_matches(path, candidates, n=5, cutoff=0.4)
        return _html_error(404, "file_missing", repo_id=repo_id, path=path, ref=ref, suggestions=suggestions, theme=theme)
    logger.error("문서를 그리지 못했습니다: %s/%s (status %s)", repo_id, path, status_code)
    return _html_error(500, "failed", repo_id=repo_id, path=path, ref=ref, theme=theme)


@app.get("/", dependencies=[Depends(verify_viewer_access)])
def view_index():
    repos = config.repositories
    if repos:
        cards = []
        for r in repos:
            label = ui.STATUS_LABELS.get(get_repo_manager(r.id).status, "")
            status = f'<span class="repo-card-meta">{label}</span>' if label else ""
            cards.append(
                f'<li data-repo-id="{ui.esc(r.id)}"><a class="repo-card" href="{ui.esc(_view_url(quote(r.id, safe="")))}">'
                f'<span class="repo-card-name">{ui.esc(r.name)}</span>'
                f'<span class="repo-card-meta">기본 브랜치 {ui.esc(r.default_ref)}</span>{status}</a></li>'
            )
        listing = f'<ul class="repo-grid">{"".join(cards)}</ul>'
    else:
        listing = ('<div class="empty-state"><p>등록된 저장소가 없습니다. '
                   '.synctx.yaml의 repositories에 저장소를 추가하세요.</p></div>')
    body = (ui.render_mobile_nav(controls=_nav_controls(None, None, "github-light"))
            + f'<div class="layout layout-home"><main id="main" class="home" tabindex="-1">'
            f'<h1 class="page-title">저장소</h1>{listing}{RECENT_SECTION}</main></div>')
    html = ui.page(view_prefix=VIEW_PREFIX, title="저장소", theme="github-light",
                   header=_shell_header(None, None, "github-light"), body=body, page_class="page-home")
    return HTMLResponse(content=html)


@app.get("/search", dependencies=[Depends(verify_viewer_access)])
async def view_search(q: str | None = None, repo: str | None = None, ref: str | None = None,
                      ref_repo: str | None = None):
    repo = repo or None
    if repo and not _repo_known(repo):
        return _html_error(404, "repo_missing", repo_id=repo)
    # ref는 그 브랜치가 속한 저장소를 다시 찾을 때만 쓴다. 저장소를 바꾸면 버린다
    ref = ref if repo and ref and ref_repo == repo else None
    query = search_mod.normalize_query(q)
    results = await _run_search(query, repo, ref) if query is not None else None
    theme = "github-light"
    title = f"\u2018{query}\u2019 검색 결과" if query is not None else "문서 검색"
    body = ui.render_mobile_nav(controls=_nav_controls(repo, ref, theme)) + ui.render_search_page(
        view_prefix=VIEW_PREFIX, query=query or "", raw_query=q or "", repos=config.repositories,
        active_repo=repo, ref=ref, results=results)
    html = ui.page(view_prefix=VIEW_PREFIX, title=title, theme=theme, header=_shell_header(repo, ref, theme),
                   body=body, repo_id=repo or "", ref=ref or "", page_class="page-search")
    return HTMLResponse(content=html)


def _collab_repos():
    return [repo for repo in config.repositories if repo.collab.enabled]


def _collect_meetings(repo_id: str | None) -> list[meeting_index.RepoMeetings]:
    repos = [repo for repo in _collab_repos() if not repo_id or repo.id == repo_id]
    return [meeting_index.collect(repo.id, repo.name, get_repo_manager(repo.id)) for repo in repos]


def _meeting_filters(repo, channel, presenter, q, start, end, descending, now, collected):
    entries = [m for result in collected for m in result.meetings]
    return entries, dict(now=now, repo=repo or None, channel=channel or None, presenter=presenter or None, q=q or None,
                         start=meeting_index.parse_date(start), end=meeting_index.parse_date(end), descending=descending)


def _meeting_json(m: meeting_index.MeetingEntry, now) -> dict:
    doc_url = f"{VIEW_PREFIX}/{quote(m.repo_id, safe='')}/{quote(m.path)}"

    def memo(items):
        return [{"text": i.text, "owner": i.owner, "due": i.due, "quote": i.quote} for i in items]

    return {
        "repo_id": m.repo_id, "repo_name": m.repo_name, "path": m.path, "id": m.id, "title": m.title,
        "starts_at": meeting_card.to_kst(m.starts_at).isoformat(),
        "ends_at": meeting_card.to_kst(m.starts_at + timedelta(minutes=m.duration_minutes)).isoformat(),
        "duration_minutes": m.duration_minutes, "channel": m.channel, "presenters": m.presenters,
        "cancelled": m.cancelled, "status": meeting_index.status_of(m, now),
        "url": doc_url, "present_url": f"{doc_url}?mode=present",
        "decisions": memo(m.decisions), "actions": memo(m.actions), "memo_error": m.memo_error,
        "jitsi": jitsi.public_info(config.collab.jitsi, m),
    }


def _require_collab_repo(repo_id: str | None) -> None:
    if repo_id and not any(repo.id == repo_id for repo in _collab_repos()):
        raise HTTPException(status_code=404, detail=f"Meeting repository not found: {repo_id}")


def _verify_jitsi_access(x_forwarded_groups: str = Header(default="")):
    try:
        verify_viewer_access(x_forwarded_groups)
    except HTTPException:
        viewer_jitsi_token_failures_total.labels(reason="forbidden").inc()
        raise


@app.get("/api/meetings/{repo_id}/{meeting_id}/jitsi", dependencies=[Depends(_verify_jitsi_access)])
def meeting_jitsi(repo_id: str, meeting_id: str, request: Request, x_forwarded_user: str = Header(default="")):
    try:
        _require_collab_repo(repo_id)
        collected = _collect_meetings(repo_id)
        found = [m for result in collected for m in result.meetings if m.id == meeting_id]
        if not found and any(result.status in meeting_index.FAILED_STATUSES for result in collected):
            raise LookupError("meeting repository unavailable")
        if len(found) != 1:
            raise HTTPException(status_code=404, detail="회의를 찾지 못했습니다")
    except HTTPException:
        viewer_jitsi_token_failures_total.labels(reason="not_found").inc()
        raise
    except Exception as exc:
        viewer_jitsi_token_failures_total.labels(reason="unavailable").inc()
        logger.warning("jitsi_meeting_failed repo_id=%s error=%s", repo_id, type(exc).__name__)
        raise HTTPException(status_code=503, detail={"reason": "unavailable", "message": "회의를 읽지 못했습니다"}) from None
    reason = jitsi.availability(config.collab.jitsi, found[0], datetime.now(timezone.utc))
    if reason:
        viewer_jitsi_token_failures_total.labels(reason=reason).inc()
        raise HTTPException(status_code=409, detail={"reason": reason, "message": jitsi.REASONS[reason]})
    try:
        payload = jitsi.issue_token(config.collab.jitsi, jitsi_keys, repo_id, found[0], x_forwarded_user,
                                    presenter_identity=request.headers.get(config.collab.jitsi.presenter_header, ""),
                                    authz_enabled=config.authz.enabled)
    except jitsi.JitsiError as exc:
        viewer_jitsi_token_failures_total.labels(reason=exc.reason).inc()
        raise HTTPException(status_code=409, detail={"reason": exc.reason, "message": str(exc)}) from None
    viewer_jitsi_tokens_issued_total.inc()
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})


@app.get("/api/meetings", dependencies=[Depends(verify_viewer_or_service_access)])
def list_meetings(repo: str | None = None, status: str | None = None, channel: str | None = None,
                  presenter: str | None = None, q: str | None = None, start: str | None = Query(None, alias="from"),
                  end: str | None = Query(None, alias="to"), order: str | None = None):
    _require_collab_repo(repo)
    if status and status not in meeting_index.STATUSES:
        raise HTTPException(status_code=400, detail="status must be one of: " + ", ".join(meeting_index.STATUSES))
    now = datetime.now(timezone.utc)
    collected = _collect_meetings(repo or None)
    entries, filters = _meeting_filters(repo, channel, presenter, q, start, end, order == "desc", now, collected)
    meetings = meeting_index.filter_meetings(entries, status=status, **filters)
    return {
        "now": meeting_card.to_kst(now).isoformat(),
        "repos": [{"id": r.repo_id, "name": r.repo_name, "status": r.status, "truncated": r.truncated} for r in collected],
        "meetings": [_meeting_json(m, now) for m in meetings],
        "invalid": [{"repo_id": i.repo_id, "repo_name": i.repo_name, "path": i.path, "reason": i.reason}
                    for r in collected for i in r.invalid],
    }


@app.get("/meetings", dependencies=[Depends(verify_viewer_access)])
def view_meetings(tab: str | None = None, repo: str | None = None, channel: str | None = None,
                  presenter: str | None = None, owner: str | None = None, q: str | None = None,
                  start: str | None = Query(None, alias="from"), end: str | None = Query(None, alias="to"),
                  order: str | None = None):
    repo = repo or None
    if repo and not any(r.id == repo for r in _collab_repos()):
        return _html_error(404, "repo_missing", repo_id=repo)
    theme = "github-light"
    now = datetime.now(timezone.utc)
    collab = _collab_repos()
    collected = _collect_meetings(None)
    everything = [m for result in collected for m in result.meetings]
    entries, filters = _meeting_filters(repo, channel, presenter, q, start, end, order == "desc", now, collected)
    meetings = meeting_index.filter_meetings(entries, **filters)
    choices = {
        "repos": [(r.id, r.name) for r in collab],
        "channels": sorted({m.channel for m in everything}),
        "presenters": sorted({p for m in everything for p in m.presenters}),
        "owners": sorted({a.owner for m in everything for a in m.actions if a.owner}),
    }
    query = {"repo": repo, "channel": channel, "presenter": presenter, "owner": owner, "q": q,
             "from": start if filters["start"] else None, "to": end if filters["end"] else None, "order": order}
    visible = [r for r in collected if not repo or r.repo_id == repo]
    body = ui.render_mobile_nav(controls=_nav_controls(None, None, theme)) + ui.render_meetings_page(
        view_prefix=VIEW_PREFIX, tab=tab or "all", query={k: v for k, v in query.items() if v}, choices=choices,
        meetings=meetings, invalid=[i for r in visible for i in r.invalid],
        repo_notes=[(r.repo_name, r.status) for r in visible if r.status != "ok"],
        truncated=any(r.truncated for r in visible), now=now, has_repos=bool(collab))
    html = ui.page(view_prefix=VIEW_PREFIX, title="회의", theme=theme, header=_shell_header(None, None, theme),
                   body=body, page_class="page-meetings")
    return HTMLResponse(content=html)


@app.get("/{repo_id}", dependencies=[Depends(verify_viewer_access)])
def view_repo(repo_id: str, ref: str | None = None, path: str | None = None):
    if repo_id in RESERVED_PATHS:
        raise HTTPException(status_code=404, detail="Not found")
    if not _repo_known(repo_id):
        return _html_error(404, "repo_missing", repo_id=repo_id)
    manager = get_repo_manager(repo_id)
    repo = manager.profile
    effective_ref = ref if ref and ref != repo.default_ref else None
    try:
        entries = manager.list_tree("", ref=effective_ref)
    except Exception:
        logger.warning("저장소 루트를 읽지 못했습니다: %s", repo_id, exc_info=True)
        entries = []
    dir_count = sum(1 for e in entries if e.type == "dir")
    file_count = sum(1 for e in entries if e.type == "file")
    readme = next((e for e in entries if e.type == "file" and e.name.lower() == "readme.md"), None)
    ref_first = f"?ref={quote(ref, safe='')}" if ref else ""
    readme_btn = ""
    if readme:
        readme_href = _view_url(f"{quote(repo_id, safe='')}/{quote(readme.path)}") + ref_first
        readme_btn = f'<a class="btn btn-primary" href="{ui.esc(readme_href)}">README 열기</a>'
    branch_label = ui.esc(ref or repo.default_ref) + (" (기본)" if (ref or repo.default_ref) == repo.default_ref else "")
    ref_display = f" ({ui.esc(ref)})" if ref and ref != repo.default_ref else ""
    commit = _commit_line(manager, "", effective_ref)
    expand_attr = f' data-expand-path="{ui.esc(path)}"' if path else ""
    tree_html = _build_tree_html(repo_id, "", ref=effective_ref, link_ref=ref)
    body = f'''<div class="layout layout-repo">
  <nav id="shell-nav" class="shell-nav" aria-label="파일">
    {ui.NAV_CLOSE}
    {_nav_controls(repo_id, ref, "github-light")}
    {TREE_FILTER}
    <div class="tree" id="file-tree"{expand_attr}>{tree_html}</div>
    {RECENT_SECTION}
  </nav>
  {ui.NAV_RESIZER}
  <div class="nav-backdrop" data-action="close-nav" hidden></div>
  <main id="main" class="viewer-content" tabindex="-1">
    <div class="breadcrumb"><a href="{VIEW_PREFIX}/">저장소</a><span aria-hidden="true">/</span><span class="current">{ui.esc(repo.name)}{ref_display}</span></div>
    <section class="info-card" aria-label="저장소 정보">
      <h1>{ui.esc(repo.name)}</h1>
      <dl>
        <dt>브랜치</dt><dd>{branch_label}</dd>
        <dt>구성</dt><dd>폴더 {dir_count}개 · 파일 {file_count}개</dd>
        <dt>최근</dt><dd class="doc-meta">{commit or "정보 없음"}</dd>
      </dl>
      {readme_btn}
    </section>
    <p class="hint">왼쪽 파일 트리에서 문서를 고르거나 위의 문서 검색을 쓰세요.</p>
  </main>
</div>'''
    html = ui.page(view_prefix=VIEW_PREFIX, title=str(repo.name), theme="github-light",
                   header=_shell_header(repo_id, ref, "github-light"), body=body,
                   repo_id=repo_id, ref=ref or "", page_class="page-repo")
    return HTMLResponse(content=html)


_encode_for_script = meeting_mode.encode_for_script


def _sanitize_html(html_str: str) -> str:
    """사용자 생성 HTML에서 위험한 태그를 제거."""
    import re
    # 중첩 태그 우회 방지를 위해 루프를 돌며 <script> 및 <iframe> 제거
    prev = ""
    while prev != html_str:
        prev = html_str
        html_str = re.sub(r'<script\b[^>]*>.*?</script>', '', html_str, flags=re.DOTALL | re.IGNORECASE)
        html_str = re.sub(r'<iframe\b[^>]*>.*?</iframe>', '', html_str, flags=re.DOTALL | re.IGNORECASE)
        
    html_str = re.sub(r'\bon\w+\s*=\s*["\'][^"\']*["\']', '', html_str, flags=re.IGNORECASE)
    html_str = re.sub(r'\bon\w+\s*=\s*\S+', '', html_str, flags=re.IGNORECASE)
    return html_str


def _build_toolbar(repo_id: str, path: str, is_md: bool, front_matter: dict, ref_param: str,
                   meeting_href: str | None = None, present_href: str | None = None) -> str:
    download = f"{VIEW_PREFIX}/api/repos/{repo_id}/download?path={quote(path)}{ref_param}"
    is_marp = is_md and bool(front_matter.get("marp", False)) and _service_enabled("marp")
    buttons = []
    if meeting_href:
        buttons.append(f'<a class="btn btn-primary" href="{html_lib.escape(meeting_href)}">회의 모드로 열기</a>')
    if present_href:
        buttons.append(f'<a class="btn" href="{html_lib.escape(present_href)}">발표하기</a>')
    if is_marp:
        preview_class = "btn" if meeting_href else "btn btn-primary"
        buttons.append(f'<button type="button" class="{preview_class}" data-action="marp-preview">슬라이드 미리보기</button>')
        buttons.append('<button type="button" class="btn" data-action="marp-pdf">PDF 만들기</button>')
        if config.marp.publish_repo:
            buttons.append('<button type="button" class="btn" data-action="marp-publish">출판…</button>')
    if path.endswith(".drawio") and _service_enabled("drawio"):
        buttons.append('<a class="btn" href="/drawio/" target="_blank" rel="noopener">Draw.io</a>')
    policy = _html_embed_policy(repo_id)
    if PurePosixPath(path).suffix.lower() in (".html", ".htm") and policy.enabled and policy.render_files:
        buttons.append(ui.render_html_links(view_prefix=VIEW_PREFIX, repo_id=repo_id, path=path, ref_param=ref_param))
    links = [f'<a href="{html_lib.escape(download)}">원본</a>']
    if is_md:
        links.append(f'<a href="{html_lib.escape(download + "&format=html")}">HTML</a>')
        if _service_enabled("marp"):
            links.append(f'<a href="{html_lib.escape(download + "&format=pdf")}">PDF</a>')
    buttons.append(f'<details class="menu"><summary class="btn">내려받기</summary>'
                   f'<div class="menu-list">{"".join(links)}</div></details>')
    return "".join(buttons)


def _repo_display_name(repo_url: str) -> str:
    """화면에 보일 저장소 이름. URL에 든 사용자명·토큰이 문서 HTML에 실리지 않게 경로만 쓴다."""
    parts = urlsplit(repo_url)
    path = parts.path if parts.scheme else repo_url.split(":", 1)[-1]
    segments = [s for s in path.rstrip("/").removesuffix(".git").split("/") if s]
    return "/".join(segments[-2:]) or (parts.hostname or "")


def _publish_dialog(path: str) -> str:
    repo_url = config.marp.publish_repo
    if not repo_url:
        return ""
    name = _repo_display_name(repo_url)
    target = f"{config.marp.publish_base_path.strip('/')}/{PurePosixPath(path).stem}.html"
    return f'''<dialog id="publish-dialog" aria-labelledby="publish-title">
  <form method="dialog">
    <h2 id="publish-title">공유 저장소에 출판할까요?</h2>
    <p>출판하면 팀이 함께 보는 슬라이드 저장소에 바로 커밋됩니다.</p>
    <dl><dt>저장소</dt><dd>{html_lib.escape(name)} ({html_lib.escape(config.marp.publish_branch)})</dd>
    <dt>파일</dt><dd><code>{html_lib.escape(target)}</code></dd></dl>
    <div class="dialog-actions"><button class="btn" value="cancel">취소</button><button class="btn btn-primary" value="confirm">출판</button></div>
  </form>
</dialog>'''


def _evidence_reader(repo_id: str, manager: GitRepoManager, effective_ref: str | None,
                     allowed_repo_ids: set[str] | None = None):
    """근거 파일 원문 읽기. 저장소마다 ref를 한 번만 해석하고, blob이 아니거나 너무 큰 파일은 읽지 않고 예외를 낸다."""
    resolved: dict[str, tuple[GitRepoManager, str]] = {}

    def read(repo: str, path: str) -> str:
        if allowed_repo_ids is not None and repo not in allowed_repo_ids:
            raise ValueError("근거 저장소를 읽을 수 없습니다")
        if repo not in resolved:
            same = repo == repo_id
            mgr = manager if same else get_repo_manager(repo)
            resolved[repo] = (mgr, mgr.resolve_ref(effective_ref if same else None))
        mgr, ref = resolved[repo]
        size = mgr.blob_size_resolved(path, ref)
        if size > config.viewer.max_render_size_bytes:
            raise ValueError(f"근거 파일이 너무 큽니다: {repo}/{path} ({size} bytes)")
        return mgr.read_resolved(path, ref).decode("utf-8")

    return read


def _render_excerpt(md: str) -> str:
    return evidence.sanitize_excerpt(render_markdown(md)["html"])


def _evidence_mapping(meeting) -> tuple[dict[str, str], tuple[str, ...], str]:
    """설정에 있는 저장소를 가리키는 매핑, 건너뛸 접두어(없는 저장소), 없는 저장소 안내 HTML."""
    valid, missing = evidence.split_references(meeting.references, {r.id for r in config.repositories})
    skipped = tuple(key for key in meeting.references if key not in valid)
    notice = meeting_mode.notice_html("근거 저장소를 찾지 못했습니다: " + ", ".join(missing)) if missing else ""
    return valid, skipped, notice


def _build_evidence(html: str, repo_id: str, path: str, manager: GitRepoManager, effective_ref: str | None,
                    references: dict[str, str], skipped: tuple[str, ...], allowed_repo_ids: set[str] | None = None):
    """근거를 조립한다. 실패하면 근거 없이 보이도록 None을 돌려준다."""
    try:
        return evidence.build(html, repo_id=repo_id, path=path, references=references, skip_prefixes=skipped,
                              read=_evidence_reader(repo_id, manager, effective_ref, allowed_repo_ids), render=_render_excerpt)
    except Exception as exc:
        logger.warning("evidence_failed repo_id=%s error=%s", repo_id, type(exc).__name__)
        return None


@app.get("/api/meetings/{repo_id}/{meeting_id}/evidence")
def meeting_evidence(repo_id: str, meeting_id: str, service_access: bool = Depends(verify_viewer_or_service_access)):
    _require_collab_repo(repo_id)
    found = [m for result in _collect_meetings(repo_id) for m in result.meetings if m.id == meeting_id]
    if len(found) != 1:
        raise HTTPException(status_code=404, detail="회의를 찾지 못했습니다")
    manager = get_repo_manager(repo_id)
    path = found[0].path
    payload = {"meeting_id": meeting_id, "evidence": [], "status": "failed", "omitted": 0}
    try:
        content = _evidence_reader(repo_id, manager, None)(repo_id, path)
        front_matter, body = parse_frontmatter(content)
        ctx = meeting_mode.resolve_meeting_context(manager.profile, path, front_matter, None)
        if not ctx.enabled or ctx.meeting.id != meeting_id:
            raise HTTPException(status_code=404, detail="회의를 찾지 못했습니다")
        allowed_repo_ids = {r.id for r in (_collab_repos() if service_access else config.repositories)}
        rendered = render_markdown(embed_view.prepare(body).text)
        built = _build_evidence(_sanitize_html(rendered["html"]), repo_id, path, manager, None,
                                ctx.meeting.references, (), allowed_repo_ids)
        if built is not None:
            payload["omitted"] = built.omitted
            payload["status"] = "partial" if built.omitted else "ok"
            payload["evidence"] = [
                {"title": e.doc, "repo_id": e.repo_id, "path": e.path,
                 "url": f"{VIEW_PREFIX}/{quote(e.repo_id, safe='')}/{quote(e.path)}"
                        + (f"#{quote(unquote(e.anchor), safe='')}" if e.anchor else "")}
                for e in built.entries
            ]
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("evidence_failed repo_id=%s error=%s", repo_id, type(exc).__name__)
    return payload


def _render_file_response(repo_id: str, path: str, theme: str = "github-light", ref: str | None = None, mode: str | None = None, layout: str | None = None):
    manager = get_repo_manager(repo_id)
    effective_ref = ref if ref and ref != manager.profile.default_ref else None
    ui_ref = effective_ref or ""
    ref_param = f"&ref={quote(ref, safe='')}" if ref else ""
    ref_param_first = f"?ref={quote(ref, safe='')}" if ref else ""
    try:
        policy = _html_embed_policy(repo_id)
        if mode == "render" and PurePosixPath(path).suffix.lower() in (".html", ".htm") and policy.enabled and policy.render_files:
            if layout not in (None, "full"):
                raise HTTPException(status_code=422, detail="유효하지 않은 렌더 레이아웃입니다")
            path = manager.validate_path(path)
            token_ref = ref or manager.profile.default_ref
            sha = manager.resolve_commit_sha(token_ref)
            manager.blob_size_resolved(path, sha)
            token = issue_render_token(repo_id, token_ref, sha, key=render_key,
                                       ttl_seconds=config.viewer.render_token_ttl_seconds)
            frame_url = _view_url(f"render/{token}/{quote(repo_id, safe='')}/{quote(path)}")
            if theme not in THEME_CSS:
                theme = "github-light"
            full = layout == "full"
            body = ui.render_html_view(frame_url=frame_url, view_prefix=VIEW_PREFIX, repo_id=repo_id,
                                       path=path, ref_param=ref_param, full=full, sha=sha)
            if not full:
                body = ui.render_mobile_nav(controls=_nav_controls(repo_id, ref, theme)) + body
            html = ui.page(
                view_prefix=VIEW_PREFIX, title=PurePosixPath(path).name, theme=theme,
                header="" if full else _shell_header(repo_id, ref, theme), body=body,
                repo_id=repo_id, ref=ui_ref,
                page_class="page-doc page-html-render" + (" page-render-full" if full else ""),
                theme_via_query=True,
            )
            return HTMLResponse(content=html, headers={"X-Synctx-Html-Render": "true"})
        try:
            blob_size = manager.file_size(path, ref=effective_ref)
        except Exception:
            blob_size = 0
        if blob_size > config.viewer.max_render_size_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"File too large for rendering ({blob_size} bytes, max {config.viewer.max_render_size_bytes})",
            )

        suffix = PurePosixPath(path).suffix.lower()
        is_md = suffix in MARKDOWN_EXTENSIONS
        language = None if is_md else get_code_language(path)
        if not is_md and language is None:
            raise HTTPException(status_code=415, detail="Unsupported file type for viewing")

        content = manager.read_file_text(path, ref=effective_ref)
        front_matter: dict = {}
        if is_md:
            front_matter, render_source = parse_frontmatter(content)
        else:
            content = _wrap_code_as_markdown(path, content, language)
            render_source = content

        if is_md:
            meeting_ctx = meeting_mode.resolve_meeting_context(manager.profile, path, front_matter, ref)
        else:
            meeting_ctx = meeting_mode.MeetingContext(False, None, None)

        prepared = embed_view.prepare(render_source) if is_md else None
        rendered = render_markdown(prepared.text if prepared else render_source)
        rendered["html"] = _sanitize_html(rendered.get("html", ""))
        found = None
        evidence_notice = ""
        if is_md:
            link_map, skipped = None, ()
            if meeting_ctx.meeting is not None:
                link_map, skipped, evidence_notice = _evidence_mapping(meeting_ctx.meeting)
                found = _build_evidence(rendered.get("html", ""), repo_id, path, manager, effective_ref, link_map, skipped)
                if found is not None:
                    rendered["html"] = found.html
            rendered["html"] = rewrite_links(rendered.get("html", ""), repo_id, path, ref=ref,
                                             link_map=link_map, keep_prefixes=skipped)
            rendered["html"] = embed_view.fill(
                rendered["html"], prepared,
                md_path=path,
                policy=_html_embed_policy(repo_id),
                read=repo_reader(manager, effective_ref),
                repo_id=repo_id,
                source_href=lambda src: f"{VIEW_PREFIX}/{repo_id}/{quote(src)}{ref_param_first}",
            )

        toc_html = generate_toc_html(rendered.get("toc", []))

        if theme not in THEME_CSS:
            theme = "github-light"
        theme_css = THEME_CSS[theme]
        hljs_theme = THEME_HLJS.get(theme, "github")
        mermaid_theme = THEME_MERMAID.get(theme, "default")

        cdn_base = config.viewer.cdn_base_url.rstrip("/")
        if config.viewer.asset_source == "bundled":
            asset_base = f"{VIEW_PREFIX}/static/vendor"
            mermaid_url = f"{asset_base}/mermaid.min.js"
            highlight_url = f"{asset_base}/highlight.min.js"
            highlight_css_url = f"{asset_base}/hljs/{hljs_theme}.min.css"
        else:
            mermaid_url = f"{cdn_base}/mermaid/10.9.1/mermaid.min.js"
            highlight_url = f"{cdn_base}/highlight.js/11.9.0/highlight.min.js"
            highlight_css_url = f"{cdn_base}/highlight.js/11.9.0/styles/{hljs_theme}.min.css"

        doc_href = f"{VIEW_PREFIX}/{repo_id}/{quote(path)}"
        presenting = mode == "present" and meeting_ctx.meeting is not None
        meeting_active = (mode == "meeting" or presenting) and meeting_ctx.enabled
        meeting_head = meeting_body = meeting_notice = meeting_exit = present_bar = ""
        jitsi_ui = None
        if meeting_ctx.meeting is not None:
            reason = jitsi.availability(config.collab.jitsi, meeting_ctx.meeting, datetime.now(timezone.utc))
            jitsi_ui = {"reason": meeting_ctx.reason or jitsi.REASONS.get(reason, "")}
            jitsi_cfg = {
                **jitsi.public_info(config.collab.jitsi, meeting_ctx.meeting),
                "enabled": config.collab.jitsi.enabled and meeting_ctx.enabled,
                "reason": meeting_ctx.reason or "", "cancelled": meeting_ctx.meeting.cancelled,
                "public_url": config.collab.jitsi.public_url if config.collab.jitsi.enabled else "",
                "endpoint": f"{VIEW_PREFIX}/api/meetings/{quote(repo_id, safe='')}/{quote(meeting_ctx.meeting.id, safe='')}/jitsi",
                "doc": f"{repo_id}:{path}",
            }
        meeting_href = present_href = None
        if meeting_active:
            meeting_cfg = meeting_mode.build_config(
                repo_id, path, meeting_ctx.meeting, readonly=False,
                export_url=meeting_mode.export_url(VIEW_PREFIX, repo_id, path),
            )
            meeting_head, meeting_body = meeting_mode.render_assets(VIEW_PREFIX, meeting_cfg)
            if not presenting:
                meeting_exit = ui.render_meeting_exit(doc_href + ref_param_first)
        elif meeting_ctx.enabled:
            meeting_href = f"{doc_href}?mode=meeting"
        elif mode == "meeting":
            meeting_notice = meeting_mode.render_notice(meeting_ctx)
        if jitsi_ui is not None:
            jitsi_head, jitsi_body = meeting_mode.render_jitsi_assets(VIEW_PREFIX, jitsi_cfg)
            meeting_head += jitsi_head
            meeting_body += jitsi_body
        if presenting:
            present_bar = ui.render_present_bar(title=meeting_ctx.meeting.title, exit_href=doc_href + ref_param_first,
                                                memo=meeting_ctx.enabled, jitsi=jitsi_ui)
        elif meeting_ctx.meeting is not None:
            present_href = f"{doc_href}?mode=present{ref_param}"
        elif mode == "present":
            meeting_notice = meeting_mode.render_notice(meeting_ctx if meeting_ctx.reason else
                meeting_mode.MeetingContext(False, "발표 화면은 회의 문서에서만 쓸 수 있습니다", None))
        meeting_notice += evidence_notice
        evidence_list = evidence_head = evidence_body = ""
        if found is not None and found.entries:
            def source_href(entry) -> str:
                href = f"{VIEW_PREFIX}/{entry.repo_id}/{quote(entry.path)}"
                if entry.repo_id == repo_id:
                    href += ref_param_first
                return href + (f"#{entry.anchor}" if entry.anchor else "")

            evidence_list = ui.render_evidence_list(found.entries, ids=found.ids, href_for=source_href)
            evidence_head = f'\n<link rel="stylesheet" href="{VIEW_PREFIX}/static/meeting/evidence.css">'
            evidence_body = (f"\n{ui.render_evidence_data(found.entries, ids=found.ids)}\n"
                             f'    <script src="{VIEW_PREFIX}/static/meeting/evidence-core.js"></script>\n'
                             f'    <script src="{VIEW_PREFIX}/static/meeting/evidence.js"></script>')
        has_mermaid = 'class="mermaid"' in rendered.get("html", "")
        if has_mermaid:
            # 발표 화면은 숨긴 장의 그림이 0 크기로 그려지므로 viewer.js가 보이는 장만 그린다
            mermaid_run = "" if presenting else "mermaid.run();"
            mermaid_scripts = (
                f'<script src="{mermaid_url}"></script>\n'
                f'    <script>mermaid.initialize({{ startOnLoad: false, theme: \'{mermaid_theme}\' }});{mermaid_run}</script>'
            )
        else:
            mermaid_scripts = ""
        toolbar_buttons = _build_toolbar(repo_id, path, is_md, front_matter, ref_param,
                                         meeting_href=meeting_href, present_href=present_href)

        title = _document_title(rendered.get("title"), front_matter, path)
        meta = _commit_line(manager, path, effective_ref)
        if meeting_ctx.enabled:
            meta += '<span class="badge">회의 문서</span>'
        download_base = f"{VIEW_PREFIX}/api/repos/{repo_id}/download?path={quote(path)}{ref_param}"
        doc_config = {
            "path": path,
            "title": title,
            "publishName": PurePosixPath(path).stem,
            "downloadHtml": download_base + "&format=html" if is_md else "",
        }
        sep = '<span aria-hidden="true">/</span>'
        breadcrumb = (f'<a href="{VIEW_PREFIX}/">저장소</a>{sep}'
                      f'<a href="{VIEW_PREFIX}/{repo_id}{html_lib.escape(ref_param_first)}">{html_lib.escape(str(manager.profile.name))}</a>'
                      f'{sep}{_build_path_trail(repo_id, path, ref, sep)}')
        meeting_card_html = ""
        if meeting_ctx.meeting is not None:
            meeting_card_html = ui.render_meeting_card(
                meeting_card.card_data(meeting_ctx.meeting, rendered.get("toc") or [], jitsi=jitsi_ui),
                dashboard_href=f"{VIEW_PREFIX}/meetings" if meeting_ctx.enabled else None)
        body = f'''<div class="layout layout-doc">
  <nav id="shell-nav" class="shell-nav" aria-label="파일과 목차">
    {ui.NAV_CLOSE}
    {_nav_controls(repo_id, ref, theme)}
    <div class="nav-tabs" role="tablist" aria-label="서랍 보기">
      <button type="button" role="tab" aria-selected="true" data-nav-tab="files">파일</button>
      <button type="button" role="tab" aria-selected="false" data-nav-tab="toc">이 문서</button>
    </div>
    <div class="nav-panel" data-nav-panel="files">
      {TREE_FILTER}
      <div class="tree" id="file-tree">{_build_tree_html(repo_id, "", ref=effective_ref, link_ref=ref, current=path)}</div>
      {RECENT_SECTION}
    </div>
    <div class="nav-panel" data-nav-panel="toc" hidden></div>
  </nav>
  {ui.NAV_RESIZER}
  <div class="nav-backdrop" data-action="close-nav" hidden></div>
  <main id="main" class="viewer-content" tabindex="-1">
    <div class="doc-head">
      <div class="breadcrumb">{breadcrumb}</div>
      {meeting_card_html}
      {meeting_exit}
      <div class="doc-meta">{meta}</div>
      <div class="doc-actions">{toolbar_buttons}</div>
    </div>
    {meeting_notice}
    {present_bar}
    <article class="markdown-body">{rendered.get("html", "")}</article>{evidence_list}
  </main>
  <aside class="shell-toc" aria-label="이 문서의 목차">
    <h2 class="nav-title">이 문서</h2>
    <div class="toc"><ul>{toc_html}</ul></div>
    <a class="toc-top" href="#main">맨 위로</a>
  </aside>
  <button type="button" class="toc-fab" data-action="open-toc">목차</button>
</div>
{_publish_dialog(path) if 'data-action="marp-publish"' in toolbar_buttons else ""}'''
        head_extra = (f"<style>{theme_css}</style>\n"
                      f'<link rel="stylesheet" href="{highlight_css_url}">\n'
                      f"{meeting_head}{evidence_head}")
        body_extra = (f'<script type="application/json" id="raw-markdown-json">{_encode_for_script({"markdown": content})}</script>\n'
                      f'<script src="{highlight_url}"></script>\n'
                      f"<script>hljs.highlightAll();</script>\n"
                      f"{mermaid_scripts}\n{meeting_body}{evidence_body}")
        html = ui.page(
            view_prefix=VIEW_PREFIX, title=title, theme=theme,
            header=_shell_header(repo_id, ref, theme), body=body,
            repo_id=repo_id, ref=ui_ref, page_class="page-doc page-present" if presenting else "page-doc",
            theme_via_query=True, doc_config=doc_config,
            head_extra=head_extra, body_extra=body_extra,
            meeting_guide=bool(meeting_exit),
        )
        return HTMLResponse(content=html)
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("render_failed repo_id=%s error=%s", repo_id, type(e).__name__)
        raise HTTPException(status_code=404, detail="File not found")


@app.get("/api/repos/{repo_id}/render", dependencies=[Depends(verify_viewer_access)])
def render_file(repo_id: str, path: str, theme: str = "github-light", ref: str | None = None, mode: str | None = None, layout: str | None = None):
    return _render_file_response(repo_id, path, theme, ref=ref, mode=mode, layout=layout)


@app.get("/api/repos/{repo_id}/meeting-export", dependencies=[Depends(verify_viewer_access)])
def meeting_export(repo_id: str, path: str, theme: str = "github-light"):
    manager = get_repo_manager(repo_id)
    try:
        blob_size = manager.file_size(path)
    except Exception:
        blob_size = 0
    if blob_size > config.viewer.max_render_size_bytes:
        raise HTTPException(status_code=413, detail="File too large for rendering")
    try:
        content = manager.read_file_text(path)
    except Exception:
        raise HTTPException(status_code=404, detail="File not found")
    front_matter, body = parse_frontmatter(content)
    ctx = meeting_mode.resolve_meeting_context(manager.profile, path, front_matter, None)
    if not ctx.enabled:
        raise HTTPException(status_code=404, detail="Meeting mode is not available for this document")
    if theme not in THEME_CSS:
        theme = "github-light"
    meeting_cfg = meeting_mode.build_config(repo_id, path, ctx.meeting, readonly=True, export_url=None)
    references, skipped, notice = _evidence_mapping(ctx.meeting)
    holder = {}

    # 본문 표시·근거 모음·근거 데이터가 한 조립 결과를 쓴다. 메모 포함 HTML은 링크를 매핑으로 고치지 않는다
    def sanitize(body_html: str) -> str:
        clean = _sanitize_html(body_html)
        found = holder["ev"] = _build_evidence(clean, repo_id, path, manager, None, references, skipped)
        return found.html if found is not None else clean

    def evidence_block() -> str:
        found = holder.get("ev")
        if found is None or not found.entries:
            return ""
        return (ui.render_evidence_list(found.entries, ids=found.ids)
                + ui.render_evidence_data(found.entries, ids=found.ids))

    try:
        document = meeting_mode.build_export_html(body, theme, meeting_cfg, sanitize=sanitize,
                                                  evidence_block=evidence_block, notice=notice)
    except meeting_mode.ExportAssemblyError as exc:
        logger.error("meeting export assembly failed: repo=%s path=%s error=%s", repo_id, path, exc)
        raise HTTPException(status_code=500, detail="Failed to assemble meeting export")
    return HTMLResponse(content=document)


# ── Kanban Board ──

KANBAN_TEMPLATE = """<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Scrum Board - {version}</title>
    <style>
        body {{ font-family: sans-serif; margin: 0; padding: 20px; background: #f4f4f4; }}
        .board {{ display: flex; gap: 20px; overflow-x: auto; padding-bottom: 20px; }}
        .column {{ background: #e9ecef; border-radius: 6px; width: 300px; min-width: 300px; padding: 10px; }}
        .column h3 {{ margin-top: 0; font-size: 16px; color: #333; padding-bottom: 10px; border-bottom: 2px solid #ccc; }}
        .card {{ background: white; padding: 15px; border-radius: 4px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); margin-bottom: 10px; cursor: pointer; }}
        .card:hover {{ box-shadow: 0 2px 5px rgba(0,0,0,0.2); }}
        .card-title {{ font-weight: bold; margin-bottom: 8px; font-size: 14px; }}
        .card-meta {{ font-size: 12px; color: #666; }}
        .badge {{ display: inline-block; padding: 2px 6px; border-radius: 12px; font-size: 11px; background: #007bff; color: white; }}
    </style>
</head>
<body>
    <h2>Scrum Board: {version}</h2>
    <p>Repository: {repo_id}</p>
    <div class="board">
        {columns_html}
    </div>
</body>
</html>"""


@app.get("/api/repos/{repo_id}/board/{version}", dependencies=[Depends(verify_viewer_access)])
def render_board(repo_id: str, version: str):
    manager = get_repo_manager(repo_id)
    try:
        entries = manager.list_tree("")
        columns = {"open": [], "in_progress": [], "closed": []}

        from synctx_common.frontmatter import parse_frontmatter

        for entry in entries:
            if entry.type == "file" and entry.name.endswith(".md"):
                content = manager.read_file_text(entry.path)
                fm, _ = parse_frontmatter(content)
                if fm and "synctx" in fm:
                    meta = fm["synctx"]
                    if meta.get("type") == "task" and meta.get("version") == version:
                        status = meta.get("status", "open").lower()
                        col_key = "open"
                        if status in ["in_progress", "doing", "active"]:
                            col_key = "in_progress"
                        elif status in ["closed", "done", "resolved"]:
                            col_key = "closed"
                        columns[col_key].append({
                            "path": entry.path,
                            "title": fm.get("title", entry.name),
                            "priority": meta.get("priority", "normal"),
                        })

        import html
        from urllib.parse import quote
        safe_version = html.escape(version)
        safe_repo_id = html.escape(repo_id)
        url_repo_id = html.escape(quote(repo_id, safe=""))
        columns_html = ""
        for col_name, cards in columns.items():
            cards_html = ""
            for card in cards:
                safe_title = html.escape(card['title'])
                safe_path = html.escape(card['path'])
                url_path = html.escape(quote(card['path']))
                safe_priority = html.escape(card['priority'])
                cards_html += f"""
                <div class="card" onclick="window.location.href='{VIEW_PREFIX}/api/repos/{url_repo_id}/render?path={url_path}'">
                    <div class="card-title">{safe_title}</div>
                    <div class="card-meta">
                        <span class="badge">{safe_priority}</span>
                        {safe_path}
                    </div>
                </div>
                """
            columns_html += f"""
            <div class="column">
                <h3>{col_name.upper()} ({len(cards)})</h3>
                {cards_html}
            </div>
            """

        html = KANBAN_TEMPLATE.format(version=safe_version, repo_id=safe_repo_id, columns_html=columns_html)
        return HTMLResponse(content=html)
    except Exception as e:
        logger.warning("board_failed repo_id=%s error=%s", repo_id, type(e).__name__)
        raise HTTPException(status_code=400, detail="Failed to build board")


def _render_html_error(status_code: int, message: str) -> HTMLResponse:
    return HTMLResponse(status_code=status_code, content=(
        '<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8"><title>HTML 렌더링 오류</title></head>'
        f'<body><p>{html_lib.escape(message)}</p></body></html>'
    ))


@app.get("/render/{token}/{repo_id}/{path:path}")
def render_html(request: Request, token: str, repo_id: str, path: str) -> HTMLResponse:
    verify_viewer_access(request.headers.get("X-Forwarded-Groups", ""))
    known = _repo_known(repo_id)

    def count(result):
        html_render_total.labels(repo_id=repo_id if known else "unknown", result=result).inc()

    try:
        ref, sha = verify_render_token(token, repo_id, key=render_key, previous_key=render_previous_key)
        if not known:
            raise RenderTokenError("알 수 없는 저장소입니다")
    except RenderTokenError as exc:
        count("denied_token")
        message = ("유효 시간이 지났습니다. 원래 Viewer 화면을 새로 고치세요" if "만료" in str(exc) else
                   "문서 주소 또는 토큰이 유효하지 않습니다. 원래 Viewer 화면에서 다시 여세요")
        return _render_html_error(403, message)
    if request.headers.get("Sec-Fetch-Dest") not in ("iframe", "frame"):
        count("denied_dest")
        return _render_html_error(403, "Viewer의 렌더 화면에서 여세요")
    policy = _html_embed_policy(repo_id)
    if not policy.enabled or not policy.render_files:
        count("disabled")
        return _render_html_error(403, "이 저장소는 HTML 렌더링이 허용되지 않습니다")
    if PurePosixPath(path).suffix.lower() not in (".html", ".htm"):
        count("not_html")
        return _render_html_error(415, "HTML 파일만 렌더링할 수 있습니다")
    manager = get_repo_manager(repo_id)
    try:
        path = manager.validate_path(path)
    except ValueError:
        return _render_html_error(400, "유효하지 않은 문서 경로입니다")
    try:
        manager.ensure_commit_available(ref, sha)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="커밋을 찾을 수 없습니다. 원래 Viewer 화면을 새로 고침하세요")
    try:
        size = manager.blob_size_resolved(path, sha)
    except (FileNotFoundError, subprocess.CalledProcessError):
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다")
    if size > policy.max_bundle_bytes:
        count("too_large")
        return _render_html_error(413, "HTML 파일이 합본 크기 상한을 넘습니다")
    try:
        result = bundle(resolved_reader(manager, sha), path, policy, frame_src="'self'")
    except BundleError as exc:
        count("bundle_error")
        status = 413 if exc.reason == "too_large" else 404 if exc.reason == "missing_file" else 422
        return _render_html_error(status, f"HTML을 불러오지 못했습니다: {exc}")
    count("ok")
    return HTMLResponse(content=result.html)


OPEN_FILE_TYPES = {
    ".html": "text/html; charset=utf-8", ".htm": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json", ".txt": "text/plain; charset=utf-8", ".woff": "font/woff", ".woff2": "font/woff2",
    ".ttf": "font/ttf", ".otf": "font/otf", ".pdf": "application/pdf", **RAW_IMAGE_TYPES,
}


# 렌더 토큰으로 저장소 파일을 합치지 않고 그대로 내보낸다. 실행 화면이 HTML 을 새 탭에서 열 때 쓰며,
# 합본이 아니므로 크기 상한이 없고 HTML 이 상대 경로로 부르는 CSS·JS·그림도 이 경로로 받는다.
@app.get("/open/{token}/{repo_id}/{path:path}")
def open_file(request: Request, token: str, repo_id: str, path: str) -> Response:
    verify_viewer_access(request.headers.get("X-Forwarded-Groups", ""))
    try:
        ref, sha = verify_render_token(token, repo_id, key=render_key, previous_key=render_previous_key)
        if not _repo_known(repo_id):
            raise RenderTokenError("알 수 없는 저장소입니다")
    except RenderTokenError as exc:
        return _render_html_error(403, "유효 시간이 지났습니다. 원래 Viewer 화면을 새로 고친 뒤 다시 여세요"
                                  if "만료" in str(exc) else "문서 주소 또는 토큰이 유효하지 않습니다. 원래 Viewer 화면에서 다시 여세요")
    policy = _html_embed_policy(repo_id)
    if not policy.enabled or not policy.render_files:
        return _render_html_error(403, "이 저장소는 HTML 렌더링이 허용되지 않습니다")
    manager = get_repo_manager(repo_id)
    try:
        path = manager.validate_path(path)
    except ValueError:
        return _render_html_error(400, "유효하지 않은 문서 경로입니다")
    try:
        manager.ensure_commit_available(ref, sha)
        content = manager.read_resolved(path, sha)
    except (FileNotFoundError, subprocess.CalledProcessError, KeyError):
        return _render_html_error(404, "파일을 찾을 수 없습니다")
    media_type = OPEN_FILE_TYPES.get(PurePosixPath(path).suffix.lower(), "application/octet-stream")
    return Response(content=content, media_type=media_type)


# catch-all이므로 다른 모든 라우트 뒤에 등록해야 한다.
@app.get("/{repo_id}/{path:path}", dependencies=[Depends(verify_viewer_access)])
def render_permalink(repo_id: str, path: str, theme: str = "github-light", ref: str | None = None, mode: str | None = None, layout: str | None = None):
    if repo_id in RESERVED_PATHS:
        raise HTTPException(status_code=404, detail="Not found")
    if not _repo_known(repo_id):
        return _html_error(404, "repo_missing", repo_id=repo_id, theme=theme)
    try:
        return _render_file_response(repo_id, path, theme, ref=ref, mode=mode, layout=layout)
    except HTTPException as exc:
        return _file_error_page(repo_id, path, ref, exc.status_code, theme)

import os
import json
from contextlib import asynccontextmanager
from pathlib import PurePosixPath

from fastapi import FastAPI, HTTPException, Request, Depends, Header, Query
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from synctx_common.config import load_config
from synctx_common.git_manager import GitRepoManager
from synctx_common.logging import configure_logging
from synctx_common.observability import register_observability_routes
from renderer import render_markdown, rewrite_links, THEME_CSS, THEME_HLJS, THEME_MERMAID

__version__ = "0.1.0"
configure_logging(service_name="viewer")
VIEW_PREFIX = "/view"

config = load_config()


@asynccontextmanager
async def lifespan(application: FastAPI):
    for repo in config.repositories:
        try:
            manager = get_repo_manager(repo.id)
            await manager.ensure()
        except Exception as e:
            print(f"Failed to initialize repo {repo.id}: {e}")
    yield


app = FastAPI(title="synctx-viewer", description="Viewer Service for synctx", version=__version__, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")
register_observability_routes(app, version=__version__, service_name="viewer")


@app.middleware("http")
async def csp_middleware(request: Request, call_next):
    response = await call_next(request)
    if "text/html" in response.headers.get("content-type", ""):
        cdn_url = config.viewer.cdn_base_url
        cdn_host = ""
        if cdn_url.startswith(("http://", "https://")):
            from urllib.parse import urlparse
            parsed = urlparse(cdn_url)
            cdn_host = f" {parsed.scheme}://{parsed.netloc}"
        elif cdn_url.startswith("//"):
            cdn_host = f" {cdn_url}"
            
        csp = (
            f"default-src 'self'; "
            f"script-src 'self' 'unsafe-inline'{cdn_host}; "
            f"style-src 'self' 'unsafe-inline'{cdn_host}; "
            f"img-src 'self' data:{cdn_host}; "
            f"font-src 'self'{cdn_host}; "
            f"connect-src 'self'{cdn_host}; "
            f"frame-src 'none'; "
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


def verify_viewer_access(x_forwarded_groups: str = Header(default="")):
    if not config.authz.enabled:
        return
    groups = [g.strip() for g in x_forwarded_groups.split(",") if g.strip()]
    allowed_roles = config.authz.role_mappings.viewer + config.authz.role_mappings.platform_admin
    if not any(role in groups for role in allowed_roles):
        raise HTTPException(status_code=403, detail="Forbidden: viewer role required")


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
        raise HTTPException(status_code=400, detail=str(e))


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
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/repos/{repo_id}/download", dependencies=[Depends(verify_viewer_access)])
async def download_file(repo_id: str, path: str, format: str = "raw", ref: str | None = None):
    manager = get_repo_manager(repo_id)
    try:
        filename_base = PurePosixPath(path).stem or "download"

        if format == "raw":
            content = manager.read_file(path, ref=ref)
            filename = os.path.basename(path.strip("/")) or "download"
            return Response(
                content=content,
                media_type="application/octet-stream",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )

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
            text_content = manager.read_file_text(path, ref=ref)
            rendered = render_markdown(text_content)
            html = _sanitize_html(rendered.get("html", ""))
            html_doc = f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>{filename_base}</title></head><body>{html}</body></html>"
            return Response(
                content=html_doc.encode("utf-8"),
                media_type="text/html",
                headers={"Content-Disposition": f'attachment; filename="{filename_base}.html"'},
            )

        if format == "pdf":
            text_content = manager.read_file_text(path, ref=ref)
            import httpx
            marp_url = config.viewer.marp_service_url.rstrip("/")
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    f"{marp_url}/api/marp/convert",
                    json={"markdown": text_content},
                )
            if resp.status_code != 200:
                raise HTTPException(status_code=502, detail="PDF conversion failed")
            return Response(
                content=resp.content,
                media_type="application/pdf",
                headers={"Content-Disposition": f'attachment; filename="{filename_base}.pdf"'},
            )

        raise HTTPException(status_code=400, detail=f"Unsupported format: {format}. Use raw, html, or pdf.")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"File not found or error: {e}")


@app.get("/raw/{repo_id}/{path:path}", dependencies=[Depends(verify_viewer_access)])
def raw_file(repo_id: str, path: str, ref: str | None = None):
    manager = get_repo_manager(repo_id)
    try:
        return Response(content=manager.read_file(path, ref=ref), media_type="application/octet-stream")
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"File not found or error: {e}")


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
        raise HTTPException(status_code=502, detail=f"Repository refresh failed: {e}")


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


# ── Theme Helpers ──

THEME_NAMES = {
    "github-light": "GitHub Light",
    "github-dark": "GitHub Dark",
    "cayman": "Cayman",
    "minimal": "Minimal",
    "hacker": "Hacker",
    "modernist": "Modernist",
    "corporate": "Corporate",
}


THEME_VARS_CSS = """
[data-theme="github-light"] {
    --primary: #0969da; --primary-light: #4493f8; --primary-lighter: #ddf4ff; --primary-dark: #0550ae;
    --bg-default: #ffffff; --bg-neutral: #f6f8fa; --bg-card: #ffffff;
    --text-primary: #1f2328; --text-secondary: #57606a; --text-disabled: #8b949e;
    --border-color: rgba(208,215,222,0.5);
    --shadow-card: 0 0 2px 0 rgba(145,158,171,0.2), 0 12px 24px -4px rgba(145,158,171,0.12);
    --shadow-sm: 0 1px 2px 0 rgba(145,158,171,0.16);
}
[data-theme="github-dark"] {
    --primary: #58a6ff; --primary-light: #79c0ff; --primary-lighter: #0d1117; --primary-dark: #388bfd;
    --bg-default: #0d1117; --bg-neutral: #161b22; --bg-card: #161b22;
    --text-primary: #e6edf3; --text-secondary: #8b949e; --text-disabled: #484f58;
    --border-color: rgba(48,54,61,0.6);
    --shadow-card: 0 0 2px 0 rgba(0,0,0,0.4), 0 12px 24px -4px rgba(0,0,0,0.3);
    --shadow-sm: 0 1px 2px 0 rgba(0,0,0,0.3);
}
[data-theme="cayman"] {
    --primary: #159957; --primary-light: #1e9b52; --primary-lighter: #e6f9ef; --primary-dark: #0e7a42;
    --bg-default: #ffffff; --bg-neutral: #f3f6fa; --bg-card: #ffffff;
    --text-primary: #606c71; --text-secondary: #819198; --text-disabled: #9aa6ac;
    --border-color: rgba(220,230,240,0.5);
    --shadow-card: 0 0 2px 0 rgba(145,158,171,0.2), 0 12px 24px -4px rgba(145,158,171,0.12);
    --shadow-sm: 0 1px 2px 0 rgba(145,158,171,0.16);
}
[data-theme="minimal"] {
    --primary: #4183c4; --primary-light: #6ba4d9; --primary-lighter: #e8f0fa; --primary-dark: #2e6da4;
    --bg-default: #ffffff; --bg-neutral: #f8f8f8; --bg-card: #ffffff;
    --text-primary: #333333; --text-secondary: #777777; --text-disabled: #999999;
    --border-color: rgba(222,222,222,0.5);
    --shadow-card: 0 0 2px 0 rgba(145,158,171,0.2), 0 12px 24px -4px rgba(145,158,171,0.12);
    --shadow-sm: 0 1px 2px 0 rgba(145,158,171,0.16);
}
[data-theme="hacker"] {
    --primary: #b5e853; --primary-light: #c4ee7e; --primary-lighter: #1a2a1a; --primary-dark: #8fc43e;
    --bg-default: #151515; --bg-neutral: #1a1a1a; --bg-card: #1a1a1a;
    --text-primary: #b0b0b0; --text-secondary: #888888; --text-disabled: #666666;
    --border-color: rgba(51,51,51,0.6);
    --shadow-card: 0 0 2px 0 rgba(0,0,0,0.4), 0 12px 24px -4px rgba(0,0,0,0.3);
    --shadow-sm: 0 1px 2px 0 rgba(0,0,0,0.3);
}
[data-theme="modernist"] {
    --primary: #4276b6; --primary-light: #6694c8; --primary-lighter: #e8f0fa; --primary-dark: #325fa0;
    --bg-default: #f1f1f1; --bg-neutral: #e8f0fa; --bg-card: #ffffff;
    --text-primary: #474747; --text-secondary: #7a8a96; --text-disabled: #9aa6ac;
    --border-color: rgba(197,209,219,0.5);
    --shadow-card: 0 0 2px 0 rgba(145,158,171,0.2), 0 12px 24px -4px rgba(145,158,171,0.12);
    --shadow-sm: 0 1px 2px 0 rgba(145,158,171,0.16);
}
[data-theme="corporate"] {
    --primary: #E01E1F; --primary-light: #E84A4B; --primary-lighter: #FDE8E8; --primary-dark: #B01819;
    --bg-default: #ffffff; --bg-neutral: #F4F6F8; --bg-card: #ffffff;
    --text-primary: #1C252E; --text-secondary: #637381; --text-disabled: #919EAB;
    --border-color: rgba(145,158,171,0.2);
    --shadow-card: 0 0 2px 0 rgba(145,158,171,0.2), 0 12px 24px -4px rgba(145,158,171,0.12);
    --shadow-sm: 0 1px 2px 0 rgba(145,158,171,0.16);
}
"""


def _build_theme_options(current="github-light"):
    html = ""
    for key, label in THEME_NAMES.items():
        sel = " selected" if key == current else ""
        html += f'<option value="{key}"{sel}>{label}</option>'
    return html


def _build_repo_options(repos, active_id=None):
    html = '<option value="">-- Select Repository --</option>'
    for r in repos:
        sel = " selected" if r.id == active_id else ""
        html += f'<option value="{r.id}"{sel}>{r.name}</option>'
    return html


# ── VIEWER_TEMPLATE ──

VIEWER_TEMPLATE = """<!DOCTYPE html>
<html lang="ko" data-theme="{active_theme}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} - synctx Viewer</title>
    <script>
        (function() {{
            if (!new URL(window.location).searchParams.has('theme')) {{
                var s = localStorage.getItem('synctx-theme');
                if (s) {{
                    var u = new URL(window.location);
                    u.searchParams.set('theme', s);
                    window.location.replace(u.toString());
                }}
            }} else {{
                var t = new URL(window.location).searchParams.get('theme');
                if (t) document.documentElement.setAttribute('data-theme', t);
            }}
        }})();
    </script>
    <style>{theme_vars_css}</style>
    <style>{theme_css}</style>
    <link rel="stylesheet" href="{cdn_base}/highlight.js/11.9.0/styles/{hljs_theme}.min.css">
    <style>
        body {{ margin: 0; padding: 0; }}
        .viewer-sidebar {{
            width: 250px; min-width: 250px; padding: 20px; height: 100vh; overflow-y: auto;
            position: fixed; top: 0; left: 0; bottom: 0;
            background: var(--bg-neutral); border-right: 1px solid var(--border-color);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            color: var(--text-primary); font-size: 14px; line-height: 1.5;
        }}
        .viewer-sidebar h3 {{ margin: 0 0 12px; font-size: 15px; font-weight: 700; }}
        .viewer-sidebar h3 a {{ color: inherit; text-decoration: none; }}
        .viewer-sidebar .path-info {{ font-size: 12px; color: var(--text-secondary); margin-bottom: 16px; word-break: break-all; }}
        .viewer-sidebar .path-info a {{ color: var(--primary); text-decoration: none; }}
        .viewer-sidebar hr {{ border: none; border-top: 1px solid var(--border-color); margin: 0 0 16px; }}
        .viewer-sidebar .toc-title {{ font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; color: var(--text-disabled); margin-bottom: 8px; }}
        .viewer-sidebar .toc ul {{ list-style: none; padding-left: 14px; margin: 0; }}
        .viewer-sidebar .toc > ul {{ padding-left: 0; }}
        .viewer-sidebar .toc li {{ margin: 3px 0; }}
        .viewer-sidebar .toc a {{ color: var(--text-secondary); text-decoration: none; font-size: 13px; }}
        .viewer-sidebar .toc a:hover {{ color: var(--primary); }}
        .viewer-content {{ margin-left: 290px; }}
        .viewer-toolbar {{
            position: sticky; top: 0; z-index: 10; padding: 10px 45px;
            border-bottom: 1px solid var(--border-color);
            display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px;
            background: var(--bg-default);
        }}
        .viewer-toolbar .breadcrumb {{ font-size: 13px; color: var(--text-secondary); }}
        .viewer-toolbar .breadcrumb a {{ color: var(--text-secondary); text-decoration: none; }}
        .viewer-toolbar .breadcrumb a:hover {{ color: var(--primary); }}
        .viewer-toolbar .actions {{ display: flex; gap: 8px; align-items: center; }}
        .viewer-toolbar select {{
            padding: 5px 10px; border: 1px solid var(--border-color); border-radius: 6px;
            font-size: 12px; background: var(--bg-card); cursor: pointer; color: var(--text-primary);
        }}
        .viewer-toolbar .btn {{
            padding: 6px 14px; border: none; border-radius: 6px; cursor: pointer;
            background: var(--primary); color: white; text-decoration: none; font-size: 13px;
        }}
        .viewer-toolbar .btn:hover {{ background: var(--primary-dark); }}
        .viewer-toolbar .btn-secondary {{ background: #6c757d; }}
        .viewer-toolbar .btn-secondary:hover {{ background: #5a6268; }}
        @media (max-width: 768px) {{
            .viewer-sidebar {{ display: none; }}
            .viewer-content {{ margin-left: 0; }}
            .viewer-toolbar {{ padding: 10px 15px; }}
        }}
    </style>
</head>
<body>
    <div class="viewer-sidebar">
        <h3><a href="{view_prefix}">synctx</a></h3>
        <div class="path-info">
            <a href="{view_prefix}/{repo_id}{ref_param_first}">{repo_id}</a> / {path}
        </div>
        <hr>
        <div class="toc-title">목차</div>
        <div class="toc"><ul>{toc_html}</ul></div>
    </div>
    <div class="viewer-content">
        <div class="viewer-toolbar">
            <div class="breadcrumb">
                <a href="{view_prefix}">synctx</a> &rsaquo;
                <a href="{view_prefix}/{repo_id}{ref_param_first}">{repo_id}</a> &rsaquo; {path}
            </div>
            <div class="actions">
                <select id="theme-select" onchange="changeTheme(this.value)">{theme_options}</select>
                {toolbar_buttons}
            </div>
        </div>
        <article class="markdown-body">{html_body}</article>
    </div>

    <script type="application/json" id="raw-markdown-json">{raw_markdown_json}</script>
    <script src="{cdn_base}/highlight.js/11.9.0/highlight.min.js"></script>
    {mermaid_scripts}
    <script>
        hljs.highlightAll();

        function changeTheme(theme) {{
            localStorage.setItem('synctx-theme', theme);
            var u = new URL(window.location);
            u.searchParams.set('theme', theme);
            window.location.href = u.toString();
        }}

        function getRawMarkdown() {{
            try {{
                var rawJson = document.getElementById('raw-markdown-json').textContent;
                return JSON.parse(rawJson).markdown;
            }} catch (e) {{
                console.error("Failed to parse raw markdown JSON", e);
                return "";
            }}
        }}

        async function convertMarpPdf() {{
            var md = getRawMarkdown();
            try {{
                var res = await fetch('/marp/api/marp/convert', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ markdown: md }})
                }});
                if (!res.ok) throw new Error('변환 실패');
                var blob = await res.blob();
                var url = window.URL.createObjectURL(blob);
                var a = document.createElement('a');
                a.href = url; a.download = 'presentation.pdf';
                document.body.appendChild(a); a.click(); a.remove();
            }} catch (e) {{ alert(e.message); }}
        }}

        async function previewMarpSlide() {{
            var md = getRawMarkdown();
            try {{
                var res = await fetch('/marp/api/marp/preview', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ markdown: md }})
                }});
                if (!res.ok) throw new Error('미리보기 변환 실패');
                var html = await res.text();
                var win = window.open('', '_blank');
                win.document.write(html);
                win.document.close();
            }} catch (e) {{ alert(e.message); }}
        }}

        async function publishMarpSlide() {{
            var md = getRawMarkdown();
            var filename = '{path}'.split('/').pop().replace('.md', '');
            try {{
                var res = await fetch('/marp/api/marp/publish', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ markdown: md, filename: filename }})
                }});
                if (!res.ok) throw new Error('출판 실패');
                var data = await res.json();
                alert('출판 성공! URL: ' + data.url);
            }} catch (e) {{ alert(e.message); }}
        }}
    </script>
</body>
</html>"""


# ── INDEX_TEMPLATE ──

INDEX_TEMPLATE = """<!DOCTYPE html>
<html lang="ko" data-theme="{active_theme}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>synctx Viewer</title>
    <script>
        (function() {{
            var saved = localStorage.getItem('synctx-theme');
            if (saved) document.documentElement.setAttribute('data-theme', saved);
        }})();
    </script>
    <style>{theme_vars_css}</style>
    <style>
        :root {{
            --radius-sm: 8px;
            --radius-md: 12px;
            --radius-lg: 16px;
            --font-primary: 'Public Sans', -apple-system, BlinkMacSystemFont, sans-serif;
            --font-mono: 'D2Coding', ui-monospace, monospace;
            --header-height: 64px;
            --sidebar-width: 320px;
        }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: var(--font-primary); background: var(--bg-default); color: var(--text-primary); }}

        .header {{
            height: var(--header-height); display: flex; align-items: center;
            padding: 0 24px; background: var(--bg-card); border-bottom: 1px solid var(--border-color);
            position: fixed; top: 0; left: 0; right: 0; z-index: 100;
        }}
        .header-logo {{ font-size: 18px; font-weight: 700; color: var(--primary); display: flex; align-items: center; gap: 8px; }}
        .header-logo svg {{ width: 28px; height: 28px; }}
        .header-actions {{ margin-left: auto; display: flex; gap: 8px; align-items: center; }}
        .theme-select {{
            padding: 6px 12px; border: 1px solid var(--border-color); border-radius: var(--radius-sm);
            background: var(--bg-card); color: var(--text-primary); font-size: 13px;
            font-family: var(--font-primary); cursor: pointer;
        }}
        .theme-select:focus {{ outline: 2px solid var(--primary); outline-offset: -2px; }}

        .layout {{ display: flex; margin-top: var(--header-height); min-height: calc(100vh - var(--header-height)); }}

        .sidebar {{
            width: var(--sidebar-width); min-width: var(--sidebar-width); background: var(--bg-neutral);
            border-right: 1px solid var(--border-color); padding: 24px 16px; overflow-y: auto;
            position: fixed; top: var(--header-height); bottom: 0; left: 0;
        }}
        .sidebar-title {{ font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; color: var(--text-disabled); margin-bottom: 12px; padding: 0 8px; }}

        .repo-select, .branch-select {{
            width: 100%; padding: 10px 12px; border: 1px solid var(--border-color);
            border-radius: var(--radius-sm); background: var(--bg-card); color: var(--text-primary);
            font-size: 14px; font-weight: 600; font-family: var(--font-primary);
            cursor: pointer; margin-bottom: 16px; appearance: auto;
        }}
        .branch-select {{ font-size: 13px; font-weight: 400; margin-bottom: 12px; }}
        .repo-select:focus, .branch-select:focus {{ outline: 2px solid var(--primary); outline-offset: -2px; }}

        .main {{ margin-left: var(--sidebar-width); flex: 1; padding: 32px; }}

        .breadcrumb {{ font-size: 14px; color: var(--text-secondary); margin-bottom: 24px; }}
        .breadcrumb a {{ color: var(--text-secondary); text-decoration: none; }}
        .breadcrumb a:hover {{ color: var(--primary); }}
        .breadcrumb .sep {{ margin: 0 8px; }}

        .card {{
            background: var(--bg-card); border-radius: var(--radius-lg);
            box-shadow: var(--shadow-card); padding: 24px; margin-bottom: 24px;
        }}
        .card-header {{ display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }}
        .card-title {{ font-size: 18px; font-weight: 700; }}
        .stat {{ font-size: 13px; color: var(--text-secondary); }}

        .repo-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 20px; }}
        .repo-card {{
            background: var(--bg-card); border-radius: var(--radius-lg); box-shadow: var(--shadow-card);
            padding: 24px; cursor: pointer; transition: transform .15s, box-shadow .15s; text-decoration: none; color: var(--text-primary);
        }}
        .repo-card:hover {{ transform: translateY(-2px); box-shadow: 0 0 2px 0 rgba(145,158,171,0.2), 0 20px 40px -4px rgba(145,158,171,0.16); }}
        .repo-card-name {{ font-size: 18px; font-weight: 700; margin-bottom: 8px; }}
        .repo-card-url {{ font-size: 13px; color: var(--text-secondary); word-break: break-all; }}

        .tree {{ font-family: var(--font-mono); font-size: 14px; }}
        .tree-entry {{
            display: flex; align-items: center; gap: 8px; padding: 6px 8px;
            border-radius: 6px; cursor: pointer; transition: background .12s; white-space: nowrap;
        }}
        .tree-entry:hover {{ background: var(--border-color); }}
        .tree-entry svg {{ width: 18px; height: 18px; flex-shrink: 0; }}
        .tree-entry .name {{ flex: 1; overflow: hidden; text-overflow: ellipsis; }}
        .tree-entry .name a {{ color: var(--text-primary); text-decoration: none; }}
        .tree-entry .name a:hover {{ color: var(--primary); }}
        .tree-children {{ padding-left: 20px; }}
        .tree-children.collapsed {{ display: none; }}
        .tree-toggle {{ transition: transform .15s; }}
        .tree-toggle.open {{ transform: rotate(90deg); }}

        .icon-dir {{ color: var(--primary); }}
        .icon-md {{ color: #2196F3; }}
        [data-theme="github-dark"] .icon-md, [data-theme="hacker"] .icon-md {{ color: #64B5F6; }}
        .icon-code {{ color: #FF9800; }}
        [data-theme="github-dark"] .icon-code, [data-theme="hacker"] .icon-code {{ color: #FFB74D; }}
        .icon-binary {{ color: var(--text-disabled); }}
        .tree-entry.disabled {{ opacity: 0.5; cursor: default; }}
        .tree-entry.disabled:hover {{ background: transparent; }}

        .loading {{ text-align: center; padding: 40px; color: var(--text-secondary); }}

        .search-box {{
            display: flex; align-items: center; gap: 8px; padding: 8px 12px;
            background: var(--bg-card); border: 1px solid var(--border-color);
            border-radius: var(--radius-sm); margin-bottom: 12px;
        }}
        .search-box input {{
            border: none; background: transparent; outline: none;
            font-size: 13px; color: var(--text-primary); width: 100%; font-family: var(--font-primary);
        }}
        .search-box svg {{ color: var(--text-disabled); flex-shrink: 0; }}

        .empty-state {{ text-align: center; padding: 60px 20px; color: var(--text-secondary); }}
        .empty-state svg {{ width: 64px; height: 64px; color: var(--text-disabled); margin-bottom: 16px; }}
        .empty-state p {{ font-size: 14px; }}

        .sidebar-hint {{ padding: 20px 8px; font-size: 13px; color: var(--text-disabled); text-align: center; }}

        @media (max-width: 768px) {{
            .sidebar {{ display: none; }}
            .main {{ margin-left: 0; }}
            .repo-grid {{ grid-template-columns: 1fr; }}
        }}
    </style>
</head>
<body>
    <header class="header">
        <div class="header-logo">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M12 2v4m0 12v4M2 12h4m12 0h4m-3.5-6.5L17 7m-10 10-1.5 1.5M20.5 17.5 19 17M5 7 3.5 5.5"/></svg>
            synctx
        </div>
        <div class="header-actions">
            <select id="index-theme-select" class="theme-select" onchange="changeTheme(this.value)">{theme_options}</select>
        </div>
    </header>

    <div class="layout">
        <aside class="sidebar">
            <div class="sidebar-title">Repositories</div>
            <select id="repo-select" class="repo-select" onchange="onRepoChange(this.value)">
                {repo_options}
            </select>
            <div id="branch-select-wrapper" style="display:{branch_select_display}">
                <select id="branch-select" class="branch-select" onchange="onBranchChange(this.value)">
                    {branch_options}
                </select>
            </div>
            <div id="sidebar-tree">{sidebar_tree_html}</div>
        </aside>
        <main class="main">
            {main_content}
        </main>
    </div>

    <script>
        var VIEW_PREFIX = '{view_prefix}';
        var ACTIVE_REPO = '{active_repo_id}';
        var ACTIVE_REF = '{active_ref}';

        function _refParam(sep) {{
            return ACTIVE_REF ? sep + 'ref=' + encodeURIComponent(ACTIVE_REF) : '';
        }}

        function changeTheme(theme) {{
            document.documentElement.setAttribute('data-theme', theme);
            localStorage.setItem('synctx-theme', theme);
        }}
        (function() {{
            var saved = localStorage.getItem('synctx-theme');
            if (saved) {{
                document.documentElement.setAttribute('data-theme', saved);
                var sel = document.getElementById('index-theme-select');
                if (sel) sel.value = saved;
            }}
        }})();

        function onRepoChange(repoId) {{
            if (repoId) {{
                window.location.href = VIEW_PREFIX + '/' + repoId;
            }} else {{
                window.location.href = VIEW_PREFIX;
            }}
        }}

        function onBranchChange(ref) {{
            if (!ACTIVE_REPO) return;
            var url = VIEW_PREFIX + '/' + ACTIVE_REPO;
            if (ref) url += '?ref=' + encodeURIComponent(ref);
            window.location.href = url;
        }}

        var ICON_DIR = '<svg class="icon-dir" viewBox="0 0 24 24" fill="currentColor"><path d="M10 4H4a2 2 0 00-2 2v12a2 2 0 002 2h16a2 2 0 002-2V8a2 2 0 00-2-2h-8l-2-2z"/></svg>';
        var ICON_MD = '<svg class="icon-md" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>';
        var ICON_CODE = '<svg class="icon-code" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/><path d="m10 13-2 2 2 2"/><path d="m14 13 2 2-2 2"/></svg>';
        var ICON_BINARY = '<svg class="icon-binary" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>';
        var FILE_ICONS = {{ markdown: ICON_MD, code: ICON_CODE, binary: ICON_BINARY }};

        function toggleDir(el) {{
            var children = el.nextElementSibling;
            var icon = el.querySelector('.tree-toggle');
            if (children) {{
                children.classList.toggle('collapsed');
                icon.classList.toggle('open');
                if (!children.classList.contains('collapsed') && children.dataset.path && !children.dataset.loaded) {{
                    loadSubtree(children, children.dataset.path);
                }}
            }}
        }}

        async function loadSubtree(container, path) {{
            container.dataset.loaded = '1';
            container.innerHTML = '<div class="loading" style="padding:8px;font-size:13px;">Loading...</div>';
            try {{
                var res = await fetch(VIEW_PREFIX + '/api/repos/' + ACTIVE_REPO + '/tree?path=' + encodeURIComponent(path) + _refParam('&'));
                if (!res.ok) throw new Error('Failed');
                var data = await res.json();
                var html = '';
                var dirs = data.entries.filter(function(e) {{ return e.type === 'dir'; }}).sort(function(a,b) {{ return a.name.localeCompare(b.name); }});
                var files = data.entries.filter(function(e) {{ return e.type === 'file'; }}).sort(function(a,b) {{ return a.name.localeCompare(b.name); }});
                for (var i = 0; i < dirs.length; i++) {{
                    var d = dirs[i];
                    html += '<div class="tree-entry" onclick="toggleDir(this)">' +
                        '<svg class="tree-toggle" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m9 18 6-6-6-6"/></svg>' +
                        ICON_DIR +
                        '<span class="name">' + d.name + '/</span></div>' +
                        '<div class="tree-children collapsed" data-path="' + d.path + '"></div>';
                }}
                for (var j = 0; j < files.length; j++) {{
                    var f = files[j];
                    var ficon = FILE_ICONS[f.icon_type] || ICON_BINARY;
                    if (f.viewable) {{
                        html += '<div class="tree-entry">' +
                            '<svg style="width:18px" viewBox="0 0 24 24"></svg>' +
                            ficon +
                            '<span class="name"><a href="' + VIEW_PREFIX + '/' + ACTIVE_REPO + '/' + f.path + _refParam('?') + '">' + f.name + '</a></span></div>';
                    }} else {{
                        html += '<div class="tree-entry disabled">' +
                            '<svg style="width:18px" viewBox="0 0 24 24"></svg>' +
                            ficon +
                            '<span class="name">' + f.name + '</span></div>';
                    }}
                }}
                container.innerHTML = html || '<div style="padding:8px;color:var(--text-disabled);font-size:13px;">Empty</div>';
            }} catch(err) {{
                container.innerHTML = '<div style="padding:8px;color:#E01E1F;">Failed to load</div>';
            }}
        }}

        function filterTree(query) {{
            var q = query.toLowerCase();
            document.querySelectorAll('#file-tree .tree-entry').forEach(function(el) {{
                var name = el.querySelector('.name').textContent.toLowerCase();
                el.style.display = !q || name.includes(q) ? '' : 'none';
            }});
        }}
    </script>
</body>
</html>"""


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


def _build_branch_options(branches, active_ref, default_ref):
    html = ""
    for b in branches:
        label = b
        if b == default_ref:
            label += " (default)"
        sel = " selected" if b == active_ref else ""
        html += f'<option value="{b}"{sel}>{label}</option>'
    return html


def _build_tree_html(repo_id, path, ref=None):
    try:
        manager = get_repo_manager(repo_id)
        entries = manager.list_tree(path, ref=ref)
    except Exception:
        return '<div class="empty-state"><p>Failed to load file tree.</p></div>'

    ref_param = f"?ref={ref}" if ref else ""
    dirs = sorted([e for e in entries if e.type == "dir"], key=lambda e: e.name)
    files = sorted([e for e in entries if e.type == "file"], key=lambda e: e.name)

    html = ""
    for e in dirs:
        html += f'''<div class="tree-entry" onclick="toggleDir(this)">
            <svg class="tree-toggle" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m9 18 6-6-6-6"/></svg>
            {DIR_ICON_SVG}
            <span class="name">{e.name}/</span>
        </div>
        <div class="tree-children collapsed" data-path="{e.path}"></div>'''
    for e in files:
        icon = _file_icon_svg(e.path)
        if is_viewable(e.path):
            file_url = _view_url(f"{repo_id}/{e.path}") + ref_param
            html += f'''<div class="tree-entry">
            <svg style="width:18px" viewBox="0 0 24 24"></svg>
            {icon}
            <span class="name"><a href="{file_url}">{e.name}</a></span>
        </div>'''
        else:
            html += f'''<div class="tree-entry disabled">
            <svg style="width:18px" viewBox="0 0 24 24"></svg>
            {icon}
            <span class="name">{e.name}</span>
        </div>'''
    if not html:
        html = '<div style="padding:8px;color:var(--text-disabled);font-size:13px;">Empty repository</div>'
    return html


# ── View Routes ──

RESERVED_PATHS = {"api", "raw", "static", "healthz", "readyz", "version", "metrics", "docs", "openapi.json", "redoc"}


@app.get("/", dependencies=[Depends(verify_viewer_access)])
def view_index():
    repos = config.repositories
    repo_options = _build_repo_options(repos)

    if repos:
        cards = ""
        for r in repos:
            cards += f'''<a class="repo-card" href="{_view_url(r.id)}">
                <div class="repo-card-name">{r.name}</div>
                <div class="repo-card-url">{r.url}</div>
            </a>'''
        main_content = f'''
            <div class="breadcrumb">Repositories</div>
            <div class="repo-grid">{cards}</div>'''
    else:
        main_content = '''
            <div class="empty-state">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                    <path d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z"/>
                </svg>
                <p>등록된 저장소가 없습니다.</p>
            </div>'''

    sidebar_tree_html = '<div class="sidebar-hint">저장소를 선택하면 파일 트리가 표시됩니다.</div>'

    html = INDEX_TEMPLATE.format(
        repo_options=repo_options,
        sidebar_tree_html=sidebar_tree_html,
        main_content=main_content,
        view_prefix=VIEW_PREFIX,
        active_repo_id="",
        theme_vars_css=THEME_VARS_CSS,
        active_theme="github-light",
        theme_options=_build_theme_options(),
        branch_options="",
        branch_select_display="none",
        active_ref="",
    )
    return HTMLResponse(content=html)


@app.get("/{repo_id}", dependencies=[Depends(verify_viewer_access)])
def view_repo(repo_id: str, ref: str | None = None):
    if repo_id in RESERVED_PATHS:
        raise HTTPException(status_code=404, detail="Not found")

    manager = get_repo_manager(repo_id)
    repos = config.repositories
    repo_options = _build_repo_options(repos, active_id=repo_id)
    repo = manager.profile

    active_ref = ref or ""
    effective_ref = ref if ref and ref != repo.default_ref else None

    try:
        branches = manager.list_branches()
    except Exception:
        branches = [repo.default_ref]
    branch_options = _build_branch_options(branches, ref or repo.default_ref, repo.default_ref)

    try:
        entries = manager.list_tree("", ref=effective_ref)
        dir_count = sum(1 for e in entries if e.type == "dir")
        file_count = sum(1 for e in entries if e.type == "file")
    except Exception:
        dir_count = 0
        file_count = 0

    tree_html = _build_tree_html(repo_id, "", ref=effective_ref)

    sidebar_tree_html = f'''
        <div class="search-box">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
            <input type="text" placeholder="Filter files..." oninput="filterTree(this.value)">
        </div>
        <div class="tree" id="file-tree">{tree_html}</div>'''

    ref_display = f" ({ref})" if ref and ref != repo.default_ref else ""
    main_content = f'''
        <div class="breadcrumb">
            <a href="{VIEW_PREFIX}">Repositories</a><span class="sep">&rsaquo;</span>{repo.name}{ref_display}
        </div>
        <div class="card">
            <div class="card-header">
                <div class="card-title">{repo.name}</div>
                <div class="stat">{dir_count} folders &middot; {file_count} files</div>
            </div>
            <div style="font-size:13px;color:var(--text-secondary);word-break:break-all;">{repo.url}</div>
        </div>
        <div class="empty-state" style="padding:40px;">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="width:48px;height:48px;">
                <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/>
                <polyline points="14 2 14 8 20 8"/>
                <line x1="16" y1="13" x2="8" y2="13"/>
                <line x1="16" y1="17" x2="8" y2="17"/>
            </svg>
            <p>왼쪽 트리에서 파일을 선택하면 문서가 표시됩니다.</p>
        </div>'''

    html = INDEX_TEMPLATE.format(
        repo_options=repo_options,
        sidebar_tree_html=sidebar_tree_html,
        main_content=main_content,
        view_prefix=VIEW_PREFIX,
        active_repo_id=repo_id,
        theme_vars_css=THEME_VARS_CSS,
        active_theme="github-light",
        theme_options=_build_theme_options(),
        branch_options=branch_options,
        branch_select_display="block",
        active_ref=active_ref,
    )
    return HTMLResponse(content=html)


_BACKSLASH = chr(92)
_SCRIPT_UNSAFE = ("<", ">", "&", chr(0x2028), chr(0x2029))


def _encode_for_script(value) -> str:
    """<script> 안에 넣을 JSON. <, >, &, U+2028, U+2029를 6글자 유니코드 이스케이프로 바꾼다."""
    text = json.dumps(value, ensure_ascii=False)
    for ch in _SCRIPT_UNSAFE:
        text = text.replace(ch, f"{_BACKSLASH}u{ord(ch):04x}")
    return text


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


def _render_file_response(repo_id: str, path: str, theme: str = "github-light", ref: str | None = None):
    manager = get_repo_manager(repo_id)
    effective_ref = ref if ref and ref != manager.profile.default_ref else None
    ref_param = f"&ref={ref}" if ref else ""
    ref_param_first = f"?ref={ref}" if ref else ""
    try:
        try:
            blob_size = manager.file_size(path, ref=effective_ref)
        except Exception:
            blob_size = 0
        if blob_size > config.viewer.max_render_size_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"File too large for rendering ({blob_size} bytes, max {config.viewer.max_render_size_bytes})",
            )

        content = manager.read_file_text(path, ref=effective_ref)

        suffix = PurePosixPath(path).suffix.lower()
        is_md = suffix in MARKDOWN_EXTENSIONS
        if not is_md:
            language = get_code_language(path)
            if language is None:
                raise HTTPException(status_code=415, detail="Unsupported file type for viewing")
            content = _wrap_code_as_markdown(path, content, language)

        rendered = render_markdown(content)
        rendered["html"] = _sanitize_html(rendered.get("html", ""))
        if is_md:
            rendered["html"] = rewrite_links(rendered.get("html", ""), repo_id, path)

        toc_html = generate_toc_html(rendered.get("toc", []))

        if theme not in THEME_CSS:
            theme = "github-light"
        theme_css = THEME_CSS[theme]
        hljs_theme = THEME_HLJS.get(theme, "github")
        mermaid_theme = THEME_MERMAID.get(theme, "default")
        theme_options = _build_theme_options(theme)

        cdn_base = config.viewer.cdn_base_url.rstrip("/")

        has_mermaid = 'class="mermaid"' in rendered.get("html", "")
        if has_mermaid:
            mermaid_scripts = (
                f'<script src="{cdn_base}/mermaid/10.9.1/mermaid.min.js"></script>\n'
                f'    <script>mermaid.initialize({{ startOnLoad: false, theme: \'{mermaid_theme}\' }});mermaid.run();</script>'
            )
        else:
            mermaid_scripts = ""

        toolbar_buttons = ""
        if is_md:
            from synctx_common.frontmatter import parse_frontmatter
            try:
                original_content = manager.read_file_text(path, ref=effective_ref)
                fm, _ = parse_frontmatter(original_content)
            except Exception:
                fm = None
            is_marp = fm.get("marp", False) if fm else False
            if is_marp:
                toolbar_buttons += '<button class="btn" onclick="convertMarpPdf()">PDF (Marp)</button>'
                toolbar_buttons += '<button class="btn btn-secondary" onclick="previewMarpSlide()">미리보기 (Marp)</button>'
                toolbar_buttons += '<button class="btn btn-secondary" onclick="publishMarpSlide()">출판 (Marp)</button>'
        if path.endswith(".drawio"):
            toolbar_buttons += '<a class="btn btn-secondary" href="/drawio/" target="_blank">Draw.io</a>'
        if not toolbar_buttons:
            toolbar_buttons += f'<a class="btn" href="{VIEW_PREFIX}/api/repos/{repo_id}/download?path={path}{ref_param}">다운로드</a>'

        html = VIEWER_TEMPLATE.format(
            title=rendered.get("title", "Document"),
            theme_vars_css=THEME_VARS_CSS,
            active_theme=theme,
            theme_css=theme_css,
            hljs_theme=hljs_theme,
            mermaid_theme=mermaid_theme,
            view_prefix=VIEW_PREFIX,
            cdn_base=cdn_base,
            repo_id=repo_id,
            path=path,
            toc_html=toc_html,
            theme_options=theme_options,
            toolbar_buttons=toolbar_buttons,
            html_body=rendered.get("html", ""),
            raw_markdown_json=_encode_for_script({"markdown": content}),
            mermaid_scripts=mermaid_scripts,
            ref_param_first=ref_param_first,
        )
        return HTMLResponse(content=html)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"File not found or error: {e}")


@app.get("/api/repos/{repo_id}/render", dependencies=[Depends(verify_viewer_access)])
def render_file(repo_id: str, path: str, theme: str = "github-light", ref: str | None = None):
    return _render_file_response(repo_id, path, theme, ref=ref)


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
        raise HTTPException(status_code=400, detail=str(e))


# catch-all이므로 다른 모든 라우트 뒤에 등록해야 한다.
@app.get("/{repo_id}/{path:path}", dependencies=[Depends(verify_viewer_access)])
def render_permalink(repo_id: str, path: str, theme: str = "github-light", ref: str | None = None):
    if repo_id in RESERVED_PATHS:
        raise HTTPException(status_code=404, detail="Not found")
    return _render_file_response(repo_id, path, theme, ref=ref)

import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, Depends, Header
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from synctx_common.config import load_config
from synctx_common.git_manager import GitRepoManager
from synctx_common.observability import register_observability_routes
from renderer import render_markdown, rewrite_links

__version__ = "0.1.0"

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
register_observability_routes(app, version=__version__)

# RepoManager 캐시
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

@app.get("/api/repos", dependencies=[Depends(verify_viewer_access)])
def list_repos():
    return {
        "repositories": [
            {"id": r.id, "name": r.name, "url": r.url}
            for r in config.repositories
        ]
    }

@app.get("/api/repos/{repo_id}/tree", dependencies=[Depends(verify_viewer_access)])
def get_tree(repo_id: str, path: str = ""):
    manager = get_repo_manager(repo_id)
    try:
        entries = manager.list_tree(path)
        return {"path": path, "entries": [e.__dict__ for e in entries]}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/repos/{repo_id}/download", dependencies=[Depends(verify_viewer_access)])
def download_file(repo_id: str, path: str):
    manager = get_repo_manager(repo_id)
    try:
        content = manager.read_file(path)
        filename = os.path.basename(path.strip("/")) or "download"
        return Response(
            content=content,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"File not found or error: {e}")

@app.get("/raw/{repo_id}/{path:path}", dependencies=[Depends(verify_viewer_access)])
def raw_file(repo_id: str, path: str):
    manager = get_repo_manager(repo_id)
    try:
        return Response(content=manager.read_file(path), media_type="application/octet-stream")
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"File not found or error: {e}")

@app.post("/api/repos/{repo_id}/webhook", dependencies=[Depends(verify_viewer_access)])
async def repo_webhook(repo_id: str, request: Request):
    manager = get_repo_manager(repo_id)
    payload = await request.json()
    source = payload.get("after") or payload.get("checkout_sha") or "webhook"
    try:
        head_sha = await manager.fetch(source=f"webhook:{source}")
        return {"status": "ok", "repo_id": repo_id, "head_sha": head_sha}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Repository refresh failed: {e}")

VIEWER_TEMPLATE = """<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} - synctx Viewer</title>
    <style>
        body {{ font-family: sans-serif; margin: 0; padding: 0; display: flex; }}
        .sidebar {{ width: 250px; background: #f4f4f4; padding: 20px; height: 100vh; overflow-y: auto; position: fixed; }}
        .content {{ margin-left: 290px; padding: 40px; max-width: 800px; width: 100%; }}
        .toolbar {{ margin-bottom: 20px; padding: 10px; background: #e9ecef; border-radius: 4px; display: flex; gap: 10px; }}
        .btn {{ padding: 8px 16px; border: none; border-radius: 4px; cursor: pointer; background: #007bff; color: white; text-decoration: none; font-size: 14px; }}
        .btn:hover {{ background: #0056b3; }}
        .btn-secondary {{ background: #6c757d; }}
        .btn-secondary:hover {{ background: #5a6268; }}
        .markdown-body {{ line-height: 1.6; }}
        pre {{ background: #f8f9fa; padding: 15px; border-radius: 4px; overflow-x: auto; }}
        img {{ max-width: 100%; }}
    </style>
    <!-- CDN 연동 -->
    <link rel="stylesheet" href="/cdn/highlight.js/11.9.0/styles/github.min.css">
    <script src="/cdn/highlight.js/11.9.0/highlight.min.js"></script>
    <script src="/cdn/mermaid/10.9.1/mermaid.min.js"></script>
</head>
<body>
    <div class="sidebar">
        <h3>synctx Viewer</h3>
        <p>Repo: {repo_id}</p>
        <p>Path: {path}</p>
        <hr>
        <h4>목차</h4>
        <ul>
            {toc_html}
        </ul>
    </div>
    <div class="content">
        <div class="toolbar">
            {toolbar_buttons}
        </div>
        <div class="markdown-body">
            {html_body}
        </div>
    </div>

    <script type="text/markdown" id="raw-markdown">{raw_markdown}</script>
    <script>
        async function convertMarpPdf() {{
            const md = document.getElementById('raw-markdown').textContent;
            try {{
                const res = await fetch('/marp/api/marp/convert', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ markdown: md }})
                }});
                if (!res.ok) throw new Error('변환 실패');
                
                const blob = await res.blob();
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = 'presentation.pdf';
                document.body.appendChild(a);
                a.click();
                a.remove();
            }} catch (e) {{
                alert(e.message);
            }}
        }}

        async function publishMarpSlide() {{
            const md = document.getElementById('raw-markdown').textContent;
            const filename = '{path}'.split('/').pop().replace('.md', '');
            try {{
                const res = await fetch('/marp/api/marp/publish', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ markdown: md, filename: filename }})
                }});
                if (!res.ok) throw new Error('출판 실패');
                
                const data = await res.json();
                alert('출판 성공! URL: ' + data.url);
            }} catch (e) {{
                alert(e.message);
            }}
        }}
    </script>
</body>
</html>"""

def generate_toc_html(toc_tokens):
    html = ""
    for token in toc_tokens:
        html += f'<li><a href="#{token["id"]}">{token["name"]}</a>'
        if token.get("children"):
            html += "<ul>" + generate_toc_html(token["children"]) + "</ul>"
        html += "</li>"
    return html

@app.get("/api/repos/{repo_id}/render", dependencies=[Depends(verify_viewer_access)])
def render_file(repo_id: str, path: str):
    manager = get_repo_manager(repo_id)
    try:
        content = manager.read_file_text(path)
        rendered = render_markdown(content)
        rendered["html"] = rewrite_links(rendered.get("html", ""), repo_id, path)

        toc_html = generate_toc_html(rendered.get("toc", []))

        toolbar_buttons = ""
        from synctx_common.frontmatter import parse_frontmatter
        fm, _ = parse_frontmatter(content)
        is_marp = fm.get("marp", False) if fm else False
        is_drawio = path.endswith(".drawio")
        if is_marp:
            toolbar_buttons += '<button class="btn" onclick="convertMarpPdf()">PDF 다운로드 (Marp)</button>'
            toolbar_buttons += '<button class="btn btn-secondary" onclick="publishMarpSlide()">슬라이드 출판 (Marp)</button>'
        if is_drawio:
            toolbar_buttons += '<a class="btn btn-secondary" href="/drawio/" target="_blank">Draw.io 열기</a>'
        if not toolbar_buttons:
            toolbar_buttons += f'<a class="btn" href="{VIEW_PREFIX}/api/repos/{repo_id}/download?path={path}">다운로드</a>'

        html = VIEWER_TEMPLATE.format(
            title=rendered.get("title", "Document"),
            repo_id=repo_id,
            path=path,
            toc_html=toc_html,
            toolbar_buttons=toolbar_buttons,
            html_body=rendered.get("html", ""),
            raw_markdown=content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )
        return HTMLResponse(content=html)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"File not found or error: {e}")

INDEX_TEMPLATE = """<!DOCTYPE html>
<html lang="ko" data-color-scheme="light">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>synctx Viewer</title>
    <link rel="stylesheet" href="/cdn/highlight.js/11.9.0/styles/github.min.css">
    <style>
        :root {{
            --primary: #6950E8;
            --primary-light: #A996F8;
            --primary-lighter: #E4DCFD;
            --primary-dark: #3828A7;
            --grey-50: #FCFDFD;
            --grey-100: #F4F6F8;
            --grey-200: #E9ECEF;
            --grey-300: #DFE3E8;
            --grey-400: #C4CDD5;
            --grey-500: #919EAB;
            --grey-600: #637381;
            --grey-700: #454F5B;
            --grey-800: #1C252E;
            --grey-900: #141A21;
            --bg-default: #FFFFFF;
            --bg-neutral: #F4F6F8;
            --bg-card: #FFFFFF;
            --text-primary: #1C252E;
            --text-secondary: #637381;
            --text-disabled: #919EAB;
            --border-color: rgba(145,158,171,0.2);
            --radius-sm: 8px;
            --radius-md: 12px;
            --radius-lg: 16px;
            --shadow-card: 0 0 2px 0 rgba(145,158,171,0.2), 0 12px 24px -4px rgba(145,158,171,0.12);
            --shadow-sm: 0 1px 2px 0 rgba(145,158,171,0.16);
            --font-primary: 'Public Sans', -apple-system, BlinkMacSystemFont, sans-serif;
            --font-mono: 'D2Coding', ui-monospace, monospace;
            --header-height: 64px;
            --sidebar-width: 320px;
        }}
        [data-color-scheme="dark"] {{
            --bg-default: #141A21;
            --bg-neutral: #1C252E;
            --bg-card: #1C252E;
            --text-primary: #FFFFFF;
            --text-secondary: #919EAB;
            --text-disabled: #637381;
            --border-color: rgba(145,158,171,0.24);
            --shadow-card: 0 0 2px 0 rgba(0,0,0,0.4), 0 12px 24px -4px rgba(0,0,0,0.3);
            --shadow-sm: 0 1px 2px 0 rgba(0,0,0,0.3);
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
        .header-actions {{ margin-left: auto; display: flex; gap: 8px; }}
        .icon-btn {{
            width: 40px; height: 40px; border-radius: var(--radius-sm); border: none;
            background: transparent; cursor: pointer; display: flex; align-items: center; justify-content: center;
            color: var(--text-secondary); transition: background .2s;
        }}
        .icon-btn:hover {{ background: var(--bg-neutral); }}

        .layout {{ display: flex; margin-top: var(--header-height); min-height: calc(100vh - var(--header-height)); }}

        .sidebar {{
            width: var(--sidebar-width); min-width: var(--sidebar-width); background: var(--bg-neutral);
            border-right: 1px solid var(--border-color); padding: 24px 16px; overflow-y: auto;
            position: fixed; top: var(--header-height); bottom: 0; left: 0;
        }}
        .sidebar-title {{ font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; color: var(--text-disabled); margin-bottom: 16px; padding: 0 8px; }}
        .repo-item {{
            display: flex; align-items: center; gap: 12px; padding: 10px 12px;
            border-radius: var(--radius-sm); cursor: pointer; transition: background .15s;
            text-decoration: none; color: var(--text-primary);
        }}
        .repo-item:hover {{ background: var(--border-color); }}
        .repo-item.active {{ background: var(--primary-lighter); color: var(--primary-dark); }}
        [data-color-scheme="dark"] .repo-item.active {{ background: rgba(105,80,232,0.16); color: var(--primary-light); }}
        .repo-dot {{ width: 10px; height: 10px; border-radius: 50%; background: var(--grey-400); flex-shrink: 0; }}
        .repo-item.active .repo-dot {{ background: var(--primary); }}
        .repo-name {{ font-size: 14px; font-weight: 600; }}

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
        .tree-entry:hover {{ background: var(--bg-neutral); }}
        .tree-entry svg {{ width: 18px; height: 18px; flex-shrink: 0; }}
        .tree-entry .name {{ flex: 1; overflow: hidden; text-overflow: ellipsis; }}
        .tree-entry .name a {{ color: var(--text-primary); text-decoration: none; }}
        .tree-entry .name a:hover {{ color: var(--primary); }}
        .tree-children {{ padding-left: 20px; }}
        .tree-children.collapsed {{ display: none; }}
        .tree-toggle {{ transition: transform .15s; }}
        .tree-toggle.open {{ transform: rotate(90deg); }}
        .dir-icon {{ color: var(--primary); }}
        .file-icon {{ color: var(--text-disabled); }}
        .loading {{ text-align: center; padding: 40px; color: var(--text-secondary); }}

        .search-box {{
            display: flex; align-items: center; gap: 8px; padding: 8px 12px;
            background: var(--bg-neutral); border: 1px solid var(--border-color);
            border-radius: var(--radius-sm); margin-bottom: 16px;
        }}
        .search-box input {{
            border: none; background: transparent; outline: none;
            font-size: 14px; color: var(--text-primary); width: 100%; font-family: var(--font-primary);
        }}
        .search-box svg {{ color: var(--text-disabled); flex-shrink: 0; }}

        .empty-state {{ text-align: center; padding: 60px 20px; color: var(--text-secondary); }}
        .empty-state svg {{ width: 64px; height: 64px; color: var(--text-disabled); margin-bottom: 16px; }}
        .empty-state p {{ font-size: 14px; }}

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
            <button class="icon-btn" onclick="toggleTheme()" title="테마 전환">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>
            </button>
        </div>
    </header>

    <div class="layout">
        <aside class="sidebar">
            <div class="sidebar-title">Repositories</div>
            <nav id="repo-nav">{repo_nav_html}</nav>
        </aside>
        <main class="main">
            {main_content}
        </main>
    </div>

    <script>
        function toggleTheme() {{
            const html = document.documentElement;
            const current = html.getAttribute('data-color-scheme');
            html.setAttribute('data-color-scheme', current === 'dark' ? 'light' : 'dark');
            localStorage.setItem('synctx-theme', html.getAttribute('data-color-scheme'));
        }}
        (function() {{
            const saved = localStorage.getItem('synctx-theme');
            if (saved) document.documentElement.setAttribute('data-color-scheme', saved);
        }})();
    </script>
</body>
</html>"""

def _build_repo_nav_html(repos, active_id=None):
    html = ""
    for r in repos:
        active = " active" if r.id == active_id else ""
        html += f'<a class="repo-item{active}" href="{_view_url(r.id)}">'
        html += '<span class="repo-dot"></span>'
        html += f'<span class="repo-name">{r.name}</span></a>'
    return html


VIEW_PREFIX = "/view"

def _view_url(path=""):
    return f"{VIEW_PREFIX}/{path}" if path else VIEW_PREFIX


@app.get("/", dependencies=[Depends(verify_viewer_access)])
def view_index():
    repos = config.repositories
    repo_nav_html = _build_repo_nav_html(repos)

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

    html = INDEX_TEMPLATE.format(repo_nav_html=repo_nav_html, main_content=main_content)
    return HTMLResponse(content=html)


RESERVED_PATHS = {"api", "raw", "static", "healthz", "readyz", "version", "metrics", "docs", "openapi.json", "redoc"}


@app.get("/{repo_id}", dependencies=[Depends(verify_viewer_access)])
def view_repo(repo_id: str):
    if repo_id in RESERVED_PATHS:
        raise HTTPException(status_code=404, detail="Not found")

    manager = get_repo_manager(repo_id)
    repos = config.repositories
    repo_nav_html = _build_repo_nav_html(repos, active_id=repo_id)
    repo = manager.profile

    try:
        entries = manager.list_tree("")
        dir_count = sum(1 for e in entries if e.type == "dir")
        file_count = sum(1 for e in entries if e.type == "file")
    except Exception:
        dir_count = 0
        file_count = 0

    tree_html = _build_tree_html(repo_id, "")
    view_prefix = VIEW_PREFIX

    main_content = f'''
        <div class="breadcrumb">
            <a href="{view_prefix}">Repositories</a><span class="sep">&rsaquo;</span>{repo.name}
        </div>
        <div class="card">
            <div class="card-header">
                <div class="card-title">{repo.name}</div>
                <div class="stat">{dir_count} folders &middot; {file_count} files</div>
            </div>
            <div style="font-size:13px;color:var(--text-secondary);word-break:break-all;">{repo.url}</div>
        </div>
        <div class="card">
            <div class="card-header">
                <div class="card-title">Files</div>
            </div>
            <div class="search-box">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
                <input type="text" placeholder="Filter files..." oninput="filterTree(this.value)">
            </div>
            <div class="tree" id="file-tree">{tree_html}</div>
        </div>
    '''

    html = INDEX_TEMPLATE.format(repo_nav_html=repo_nav_html, main_content=main_content)
    html = html.replace('</script>\n</body>', f'''</script>
    <script>
        function toggleDir(el) {{
            const children = el.parentElement.querySelector('.tree-children');
            const icon = el.querySelector('.tree-toggle');
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
            container.innerHTML = '<div class="loading">Loading...</div>';
            try {{
                const repo_id = '{repo_id}';
                const res = await fetch('{view_prefix}/api/repos/' + repo_id + '/tree?path=' + encodeURIComponent(path));
                if (!res.ok) throw new Error('Failed');
                const data = await res.json();
                let html = '';
                const dirs = data.entries.filter(e => e.type === 'dir').sort((a,b) => a.name.localeCompare(b.name));
                const files = data.entries.filter(e => e.type === 'file').sort((a,b) => a.name.localeCompare(b.name));
                for (const e of dirs) {{
                    html += '<div class="tree-entry" onclick="toggleDir(this)">' +
                        '<svg class="tree-toggle" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m9 18 6-6-6-6"/></svg>' +
                        '<svg class="dir-icon" viewBox="0 0 24 24" fill="currentColor"><path d="M10 4H4a2 2 0 00-2 2v12a2 2 0 002 2h16a2 2 0 002-2V8a2 2 0 00-2-2h-8l-2-2z"/></svg>' +
                        '<span class="name">' + e.name + '/</span></div>' +
                        '<div class="tree-children collapsed" data-path="' + e.path + '"></div>';
                }}
                for (const e of files) {{
                    html += '<div class="tree-entry">' +
                        '<svg style="width:18px" viewBox="0 0 24 24"></svg>' +
                        '<svg class="file-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>' +
                        '<span class="name"><a href="{view_prefix}/' + repo_id + '/' + e.path + '">' + e.name + '</a></span></div>';
                }}
                container.innerHTML = html || '<div style="padding:8px;color:var(--text-disabled);font-size:13px;">Empty</div>';
            }} catch(err) {{
                container.innerHTML = '<div style="padding:8px;color:#E01E1F;">Failed to load</div>';
            }}
        }}
        function filterTree(query) {{
            const q = query.toLowerCase();
            document.querySelectorAll('#file-tree .tree-entry').forEach(el => {{
                const name = el.querySelector('.name').textContent.toLowerCase();
                el.style.display = !q || name.includes(q) ? '' : 'none';
            }});
        }}
    </script>
</body>''')
    return HTMLResponse(content=html)


def _build_tree_html(repo_id, path):
    try:
        manager = get_repo_manager(repo_id)
        entries = manager.list_tree(path)
    except Exception:
        return '<div class="empty-state"><p>Failed to load file tree.</p></div>'

    dirs = sorted([e for e in entries if e.type == "dir"], key=lambda e: e.name)
    files = sorted([e for e in entries if e.type == "file"], key=lambda e: e.name)

    html = ""
    for e in dirs:
        html += f'''<div class="tree-entry" onclick="toggleDir(this)">
            <svg class="tree-toggle" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m9 18 6-6-6-6"/></svg>
            <svg class="dir-icon" viewBox="0 0 24 24" fill="currentColor"><path d="M10 4H4a2 2 0 00-2 2v12a2 2 0 002 2h16a2 2 0 002-2V8a2 2 0 00-2-2h-8l-2-2z"/></svg>
            <span class="name">{e.name}/</span>
        </div>
        <div class="tree-children collapsed" data-path="{e.path}"></div>'''
    for e in files:
        file_url = _view_url(f"{repo_id}/{e.path}")
        html += f'''<div class="tree-entry">
            <svg style="width:18px" viewBox="0 0 24 24"></svg>
            <svg class="file-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
            <span class="name"><a href="{file_url}">{e.name}</a></span>
        </div>'''
    if not html:
        html = '<div style="padding:8px;color:var(--text-disabled);font-size:13px;">Empty repository</div>'
    return html


@app.get("/{repo_id}/{path:path}", dependencies=[Depends(verify_viewer_access)])
def render_permalink(repo_id: str, path: str):
    if repo_id in RESERVED_PATHS:
        raise HTTPException(status_code=404, detail="Not found")
    return render_file(repo_id, path)

KANBAN_TEMPLATE = """<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Scrum Board - {version}</title>
    <style>
        body { font-family: sans-serif; margin: 0; padding: 20px; background: #f4f4f4; }
        .board { display: flex; gap: 20px; overflow-x: auto; padding-bottom: 20px; }
        .column { background: #e9ecef; border-radius: 6px; width: 300px; min-width: 300px; padding: 10px; }
        .column h3 { margin-top: 0; font-size: 16px; color: #333; padding-bottom: 10px; border-bottom: 2px solid #ccc; }
        .card { background: white; padding: 15px; border-radius: 4px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); margin-bottom: 10px; cursor: pointer; }
        .card:hover { box-shadow: 0 2px 5px rgba(0,0,0,0.2); }
        .card-title { font-weight: bold; margin-bottom: 8px; font-size: 14px; }
        .card-meta { font-size: 12px; color: #666; }
        .badge { display: inline-block; padding: 2px 6px; border-radius: 12px; font-size: 11px; background: #007bff; color: white; }
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
        
        # Simple Kanban state mapping
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
                        # Map status to columns
                        col_key = "open"
                        if status in ["in_progress", "doing", "active"]: col_key = "in_progress"
                        elif status in ["closed", "done", "resolved"]: col_key = "closed"
                        
                        columns[col_key].append({
                            "path": entry.path,
                            "title": fm.get("title", entry.name),
                            "priority": meta.get("priority", "normal")
                        })
                        
        columns_html = ""
        for col_name, cards in columns.items():
            cards_html = ""
            for card in cards:
                cards_html += f"""
                <div class="card" onclick="window.location.href='/api/repos/{repo_id}/render?path={card['path']}'">
                    <div class="card-title">{card['title']}</div>
                    <div class="card-meta">
                        <span class="badge">{card['priority']}</span>
                        {card['path']}
                    </div>
                </div>
                """
            columns_html += f"""
            <div class="column">
                <h3>{col_name.upper()} ({len(cards)})</h3>
                {cards_html}
            </div>
            """
            
        html = KANBAN_TEMPLATE.format(version=version, repo_id=repo_id, columns_html=columns_html)
        return HTMLResponse(content=html)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

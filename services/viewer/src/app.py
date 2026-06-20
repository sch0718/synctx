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
            toolbar_buttons += f'<a class="btn" href="/api/repos/{repo_id}/download?path={path}">다운로드</a>'

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

@app.get("/view/{repo_id}/{path:path}", dependencies=[Depends(verify_viewer_access)])
def render_permalink(repo_id: str, path: str):
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

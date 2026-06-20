import os
import subprocess
import uuid
import logging
from pathlib import Path
from contextlib import contextmanager

from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends, Header
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from synctx_common.config import load_config, RepoAuthConfig, RepoProfile
from synctx_common.git_manager import GitRepoManager
from synctx_common.observability import register_observability_routes

logger = logging.getLogger(__name__)

__version__ = "0.1.0"
app = FastAPI(title="synctx-marp", description="Marp Service for synctx", version=__version__, root_path="/marp")

config = load_config()
register_observability_routes(app, version=__version__)

class ConvertRequest(BaseModel):
    markdown: str

class PublishRequest(BaseModel):
    markdown: str
    filename: str
    author_name: str = "synctx-publisher"
    author_email: str = "publisher@synctx.internal"

class PreviewRequest(BaseModel):
    markdown: str

def verify_publisher_access(x_forwarded_groups: str = Header(default="")):
    if not config.authz.enabled:
        return
    groups = [g.strip() for g in x_forwarded_groups.split(",") if g.strip()]
    allowed_roles = config.authz.role_mappings.publisher + config.authz.role_mappings.platform_admin
    if not any(role in groups for role in allowed_roles):
        raise HTTPException(status_code=403, detail="Forbidden: publisher role required")

def cleanup_file(path: str):
    try:
        if os.path.exists(path):
            os.unlink(path)
    except Exception as e:
        logger.error(f"Failed to cleanup {path}: {e}")

@contextmanager
def publish_lock():
    lock_dir = Path(config.marp.worktree_path)
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / ".publish.lock"
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    except FileExistsError:
        raise HTTPException(status_code=409, detail="Publish is already in progress")
    try:
        yield
    finally:
        lock_path.unlink(missing_ok=True)

def get_publish_manager() -> GitRepoManager:
    profile = RepoProfile(
        id="publish-repo",
        name="Publish Repo",
        url=config.marp.publish_repo,
        default_ref=config.marp.publish_branch,
        auth=config.repositories[0].auth if config.repositories else RepoAuthConfig(type="none"),
    )
    return GitRepoManager(profile, config.marp.worktree_path)

@app.post("/api/marp/convert", dependencies=[Depends(verify_publisher_access)])
async def convert_pdf(req: ConvertRequest, background_tasks: BackgroundTasks):
    """마크다운을 PDF로 변환하여 반환한다."""
    if len(req.markdown.encode('utf-8')) > config.marp.max_input_size_bytes:
        raise HTTPException(status_code=400, detail="Input markdown too large")

    work_dir = Path(os.getenv("MARP_WORK_DIR", "/tmp/marp-work"))
    work_dir.mkdir(parents=True, exist_ok=True)
    
    job_id = str(uuid.uuid4())
    md_path = work_dir / f"{job_id}.md"
    pdf_path = work_dir / f"{job_id}.pdf"
    
    md_path.write_text(req.markdown, encoding="utf-8")
    
    try:
        # Run marp-cli
        subprocess.run(
            ["marp", str(md_path), "--pdf", "-o", str(pdf_path)],
            check=True,
            capture_output=True,
            timeout=config.marp.conversion_timeout_seconds
        )
    except subprocess.TimeoutExpired:
        cleanup_file(str(md_path))
        cleanup_file(str(pdf_path))
        raise HTTPException(status_code=504, detail="Conversion timed out")
    except subprocess.CalledProcessError as e:
        cleanup_file(str(md_path))
        cleanup_file(str(pdf_path))
        logger.error(f"Marp CLI error: {e.stderr.decode()}")
        raise HTTPException(status_code=500, detail="Conversion failed")
        
    background_tasks.add_task(cleanup_file, str(md_path))
    background_tasks.add_task(cleanup_file, str(pdf_path))
    
    return FileResponse(
        path=pdf_path,
        filename="presentation.pdf",
        media_type="application/pdf"
    )

@app.post("/api/marp/preview", response_class=HTMLResponse, dependencies=[Depends(verify_publisher_access)])
async def preview_html(req: PreviewRequest):
    if len(req.markdown.encode("utf-8")) > config.marp.max_input_size_bytes:
        raise HTTPException(status_code=400, detail="Input markdown too large")

    work_dir = Path(os.getenv("MARP_WORK_DIR", "/tmp/marp-work"))
    work_dir.mkdir(parents=True, exist_ok=True)
    job_id = str(uuid.uuid4())
    md_path = work_dir / f"{job_id}.md"
    html_path = work_dir / f"{job_id}.html"
    md_path.write_text(req.markdown, encoding="utf-8")

    try:
        subprocess.run(
            ["marp", str(md_path), "--html", "-o", str(html_path)],
            check=True,
            capture_output=True,
            timeout=config.marp.conversion_timeout_seconds,
        )
        return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
    except subprocess.CalledProcessError as e:
        logger.error("Marp CLI error: %s", e.stderr.decode())
        raise HTTPException(status_code=500, detail="Preview conversion failed")
    finally:
        cleanup_file(str(md_path))
        cleanup_file(str(html_path))

@app.post("/api/marp/publish", dependencies=[Depends(verify_publisher_access)])
async def publish_html(req: PublishRequest):
    """마크다운을 HTML로 변환하여 출판 저장소에 커밋한다."""
    if not config.marp.publish_repo:
        raise HTTPException(status_code=500, detail="Publish repo not configured")
        
    work_dir = Path(os.getenv("MARP_WORK_DIR", "/tmp/marp-work"))
    work_dir.mkdir(parents=True, exist_ok=True)
    
    job_id = str(uuid.uuid4())
    md_path = work_dir / f"{job_id}.md"
    html_path = work_dir / f"{job_id}.html"
    
    md_path.write_text(req.markdown, encoding="utf-8")
    
    try:
        subprocess.run(
            ["marp", str(md_path), "--html", "-o", str(html_path)],
            check=True,
            capture_output=True,
            timeout=config.marp.conversion_timeout_seconds
        )
    except subprocess.CalledProcessError as e:
        cleanup_file(str(md_path))
        logger.error(f"Marp CLI error: {e.stderr.decode()}")
        raise HTTPException(status_code=500, detail="Conversion failed")
        
    html_content = html_path.read_bytes()
    cleanup_file(str(md_path))
    cleanup_file(str(html_path))
    
    with publish_lock():
        manager = get_publish_manager()
        await manager.ensure()
        target_path = f"{config.marp.publish_base_path.strip('/')}/{req.filename}"
        if not target_path.endswith(".html"):
            target_path += ".html"
        try:
            manager.commit_and_push(
                file_path=target_path,
                content=html_content,
                message=f"docs(slides): publish {req.filename}",
                author_name=req.author_name,
                author_email=req.author_email
            )
        except Exception as e:
            logger.error(f"Failed to publish to git: {e}")
            raise HTTPException(status_code=500, detail="Failed to publish to git repository")
        
    return {
        "status": "success",
        "path": target_path,
        "url": f"/slides/{req.filename}"
    }

@app.get("/api/marp/published", dependencies=[Depends(verify_publisher_access)])
async def list_published():
    if not config.marp.publish_repo:
        raise HTTPException(status_code=500, detail="Publish repo not configured")
    manager = get_publish_manager()
    await manager.ensure()
    base_path = config.marp.publish_base_path.strip("/")
    try:
        entries = manager.list_tree(base_path, recursive=True)
    except Exception:
        entries = []
    return {
        "items": [
            {"path": entry.path, "url": f"/slides/{Path(entry.path).name}"}
            for entry in entries
            if entry.type == "file" and entry.name.endswith(".html")
        ]
    }

@app.delete("/api/marp/published/{filename:path}", dependencies=[Depends(verify_publisher_access)])
async def unpublish(filename: str):
    if not config.marp.publish_repo:
        raise HTTPException(status_code=500, detail="Publish repo not configured")
    with publish_lock():
        manager = get_publish_manager()
        await manager.ensure()
        target_path = manager.validate_path(f"{config.marp.publish_base_path.strip('/')}/{filename}")
        abs_path = manager.repo_dir / target_path
        if not abs_path.exists():
            raise HTTPException(status_code=404, detail="Published file not found")
        abs_path.unlink()
        manager._run_git("rm", target_path)
        manager._run_git("commit", "-m", f"docs(slides): unpublish {filename}")
        manager._run_git("push", "origin", manager.profile.default_ref)
        return {"status": "deleted", "path": target_path}

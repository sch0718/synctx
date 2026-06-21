import asyncio
import functools
import os
import re
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
from synctx_common.logging import configure_logging
from synctx_common.observability import register_observability_routes

__version__ = "0.1.0"
configure_logging(service_name="marp")
logger = logging.getLogger(__name__)

app = FastAPI(title="synctx-marp", description="Marp Service for synctx", version=__version__, root_path="/marp")

config = load_config()
register_observability_routes(app, version=__version__, service_name="marp")

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

MERMAID_BLOCK_RE = re.compile(
    r"```mermaid\s*\n(.*?)```",
    re.DOTALL,
)

PUPPETEER_CONFIG_PATH = "/tmp/marp-work/puppeteer-config.json"


def _ensure_puppeteer_config():
    config_path = Path(PUPPETEER_CONFIG_PATH)
    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text('{"executablePath":"/usr/bin/chromium","args":["--no-sandbox"]}')


def _preprocess_mermaid_sync(markdown: str, work_dir: Path) -> tuple[str, list[str]]:
    """mermaid 코드 블록을 SVG로 변환하여 인라인 HTML로 대체한다 (동기)."""
    _ensure_puppeteer_config()
    blocks = list(MERMAID_BLOCK_RE.finditer(markdown))
    if not blocks:
        return markdown, []

    svg_files: list[str] = []
    result = markdown

    for i, match in enumerate(reversed(blocks)):
        mmd_code = match.group(1)
        svg_name = f"mermaid-{uuid.uuid4().hex[:8]}.svg"
        mmd_path = work_dir / f"{svg_name}.mmd"
        svg_path = work_dir / svg_name

        mmd_path.write_text(mmd_code, encoding="utf-8")
        mmdc_cmd = [
            "mmdc", "-i", str(mmd_path), "-o", str(svg_path),
            "-p", PUPPETEER_CONFIG_PATH,
            "-b", "transparent",
        ]
        last_exc = None
        for attempt in range(3):
            try:
                subprocess.run(mmdc_cmd, check=True, capture_output=True, timeout=60)
                last_exc = None
                break
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                last_exc = exc
                stderr = getattr(exc, "stderr", None)
                if stderr:
                    logger.warning("mmdc attempt %d for block %d stderr: %s", attempt, i, stderr.decode(errors="replace"))
                if attempt < 2:
                    import time
                    time.sleep(2)
        if last_exc is not None:
            logger.warning("mmdc failed for block %d after 3 attempts: %s", i, last_exc)
            mmd_path.unlink(missing_ok=True)
            continue
        mmd_path.unlink(missing_ok=True)

        svg_content = svg_path.read_text(encoding="utf-8")
        svg_files.append(str(svg_path))
        svg_content = re.sub(
            r'(<svg[^>]*)\bstyle="([^"]*)"',
            r'\1style="\2 max-height:60vh;"',
            svg_content,
            count=1,
        )
        replacement = f'<div style="text-align:center">{svg_content}</div>'
        result = result[:match.start()] + replacement + result[match.end():]

    return result, svg_files


def _write_and_run_marp_sync(processed_md: str, md_path: Path, output_path: Path, extra_flags: list[str], timeout: int) -> None:
    md_path.write_text(processed_md, encoding="utf-8")
    subprocess.run(
        ["marp", str(md_path), "--html", "--allow-local-files", *extra_flags, "-o", str(output_path)],
        check=True,
        capture_output=True,
        timeout=timeout,
    )


def _read_file_sync(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _read_file_bytes_sync(path: Path) -> bytes:
    return path.read_bytes()


async def preprocess_mermaid(markdown: str, work_dir: Path) -> tuple[str, list[str]]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, functools.partial(_preprocess_mermaid_sync, markdown, work_dir)
    )


async def write_and_run_marp(processed_md: str, md_path: Path, output_path: Path, extra_flags: list[str], timeout: int) -> None:
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, functools.partial(_write_and_run_marp_sync, processed_md, md_path, output_path, extra_flags, timeout)
    )


async def read_file_async(path: Path) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _read_file_sync, path)


async def read_file_bytes_async(path: Path) -> bytes:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _read_file_bytes_sync, path)


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

    processed = ""
    svg_files = []
    job_id = str(uuid.uuid4())
    md_path = work_dir / f"{job_id}.md"
    pdf_path = work_dir / f"{job_id}.pdf"

    try:
        processed, svg_files = await preprocess_mermaid(req.markdown, work_dir)
        await write_and_run_marp(processed, md_path, pdf_path, ["--pdf"], config.marp.conversion_timeout_seconds)
    except subprocess.TimeoutExpired:
        cleanup_file(str(md_path))
        cleanup_file(str(pdf_path))
        for f in svg_files:
            cleanup_file(f)
        raise HTTPException(status_code=504, detail="Conversion timed out")
    except Exception as e:
        cleanup_file(str(md_path))
        cleanup_file(str(pdf_path))
        for f in svg_files:
            cleanup_file(f)
        logger.error(f"Marp PDF Conversion failed: {e}")
        raise HTTPException(status_code=500, detail="Conversion failed")

    background_tasks.add_task(cleanup_file, str(md_path))
    background_tasks.add_task(cleanup_file, str(pdf_path))
    for f in svg_files:
        background_tasks.add_task(cleanup_file, f)

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

    processed = ""
    svg_files = []
    job_id = str(uuid.uuid4())
    md_path = work_dir / f"{job_id}.md"
    html_path = work_dir / f"{job_id}.html"

    try:
        processed, svg_files = await preprocess_mermaid(req.markdown, work_dir)
        await write_and_run_marp(processed, md_path, html_path, [], config.marp.conversion_timeout_seconds)
        html_content = await read_file_async(html_path)
        return HTMLResponse(content=html_content)
    except Exception as e:
        logger.error("Marp Preview failed: %s", e)
        raise HTTPException(status_code=500, detail="Preview conversion failed")
    finally:
        cleanup_file(str(md_path))
        cleanup_file(str(html_path))
        for f in svg_files:
            cleanup_file(f)

@app.post("/api/marp/publish", dependencies=[Depends(verify_publisher_access)])
async def publish_html(req: PublishRequest):
    """마크다운을 HTML로 변환하여 출판 저장소에 커밋한다."""
    if not config.marp.publish_repo:
        raise HTTPException(status_code=500, detail="Publish repo not configured")

    work_dir = Path(os.getenv("MARP_WORK_DIR", "/tmp/marp-work"))
    work_dir.mkdir(parents=True, exist_ok=True)

    processed = ""
    svg_files = []
    job_id = str(uuid.uuid4())
    md_path = work_dir / f"{job_id}.md"
    html_path = work_dir / f"{job_id}.html"

    try:
        processed, svg_files = await preprocess_mermaid(req.markdown, work_dir)
        await write_and_run_marp(processed, md_path, html_path, [], config.marp.conversion_timeout_seconds)
        html_content = await read_file_bytes_async(html_path)
    except Exception as e:
        logger.error(f"Marp Publish HTML Conversion failed: {e}")
        raise HTTPException(status_code=500, detail="Conversion failed")
    finally:
        cleanup_file(str(md_path))
        cleanup_file(str(html_path))
        for f in svg_files:
            cleanup_file(f)
    
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

MARP_BUILTIN_THEMES = [
    {"name": "default", "description": "Marp 기본 테마"},
    {"name": "gaia", "description": "Gaia 테마 (어두운 배경)"},
    {"name": "uncover", "description": "Uncover 테마 (미니멀)"},
]


@app.get("/api/marp/themes")
async def list_themes():
    """사용 가능한 Marp 테마 목록을 반환."""
    return {"themes": MARP_BUILTIN_THEMES}


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

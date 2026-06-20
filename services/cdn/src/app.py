import os
import hashlib
import json
import time
import logging
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Depends, Header, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse

from synctx_common.config import load_config
from synctx_common.observability import register_observability_routes

logger = logging.getLogger(__name__)

__version__ = "0.1.0"

app = FastAPI(title="synctx-cdn", description="CDN Service for synctx", version=__version__, root_path="/cdn")

config = load_config()
register_observability_routes(app, version=__version__)

@app.middleware("http")
async def add_cache_headers(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/") or request.url.path in {"/healthz", "/readyz", "/version", "/metrics"}:
        return response
    if response.status_code == 200:
        response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
    return response

CDN_DATA_DIR = Path(os.getenv("CDN_DATA_DIR", config.cdn.storage_path))
try:
    CDN_DATA_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    if "CDN_DATA_DIR" in os.environ:
        raise
    CDN_DATA_DIR = Path("../../cdn-data")
    CDN_DATA_DIR.mkdir(parents=True, exist_ok=True)

MANIFEST_FILE = CDN_DATA_DIR / "manifest.json"
AUDIT_LOG_FILE = CDN_DATA_DIR / "audit.log"

def get_manifest():
    if MANIFEST_FILE.exists():
        try:
            return json.loads(MANIFEST_FILE.read_text("utf-8"))
        except json.JSONDecodeError as exc:
            logger.warning("manifest_parse_failed path=%s error=%s", MANIFEST_FILE, exc)
    return {}

def save_manifest(manifest):
    tmp_file = MANIFEST_FILE.with_suffix(".tmp")
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_file, MANIFEST_FILE)

def log_audit(action: str, path: str, user: str, details: dict):
    log_entry = {
        "timestamp": time.time(),
        "action": action,
        "path": path,
        "user": user,
        "details": details
    }
    with open(AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry) + "\n")

def verify_cdn_admin(x_forwarded_groups: str = Header(default=""), x_forwarded_user: str = Header(default="unknown")):
    if not config.cdn.upload_auth.enabled:
        return x_forwarded_user
    groups = [g.strip() for g in x_forwarded_groups.split(",") if g.strip()]
    
    # authz 설정에서 cdn-admin 역할에 매핑된 그룹 확인
    allowed_roles = config.authz.role_mappings.cdn_admin + config.authz.role_mappings.platform_admin
    
    if not any(role in groups for role in allowed_roles):
        raise HTTPException(status_code=403, detail="Forbidden: cdn-admin role required")
    return x_forwarded_user

def validate_path(path_str: str) -> Path:
    normalized = os.path.normpath(path_str.strip("/"))
    if normalized.startswith("..") or normalized.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid path")
    
    parts = normalized.split("/")
    if len(parts) < 3:
        raise HTTPException(status_code=400, detail="Path must be library/version/file format")
        
    return CDN_DATA_DIR / normalized

def validate_mime_signature(ext: str, content: bytes) -> None:
    checks = {
        ".png": lambda data: data.startswith(b"\x89PNG\r\n\x1a\n"),
        ".woff": lambda data: data.startswith(b"wOFF"),
        ".woff2": lambda data: data.startswith(b"wOF2"),
    }
    check = checks.get(ext)
    if check and not check(content[:16]):
        raise HTTPException(status_code=400, detail=f"File signature does not match extension: {ext}")

@app.post("/api/cdn/upload")
async def upload_file(
    path: str = Form(...),
    file: UploadFile = File(...),
    force: bool = Query(False),
    user: str = Depends(verify_cdn_admin)
):
    target_path = validate_path(path)
    
    ext = target_path.suffix.lower()
    if ext not in config.cdn.allowed_extensions:
        raise HTTPException(status_code=400, detail=f"Extension not allowed: {ext}")
        
    if target_path.exists() and not force:
        raise HTTPException(status_code=409, detail="File already exists. Use ?force=true to overwrite.")
        
    target_path.parent.mkdir(parents=True, exist_ok=True)
    
    content = await file.read()
    file_size = len(content)
    if file_size > config.cdn.max_upload_size_bytes:
        raise HTTPException(status_code=400, detail="File too large")
    validate_mime_signature(ext, content)
        
    sha256 = hashlib.sha256(content).hexdigest()
    
    target_path.write_bytes(content)
    
    # Update manifest
    manifest = get_manifest()
    rel_path = str(target_path.relative_to(CDN_DATA_DIR))
    manifest[rel_path] = {
        "sha256": sha256,
        "size": file_size,
        "updated_at": time.time()
    }
    save_manifest(manifest)
    
    log_audit("upload", rel_path, user, {"force": force, "sha256": sha256, "size": file_size})
        
    return {"status": "success", "path": rel_path, "sha256": sha256}

@app.get("/api/cdn/list")
def list_libraries():
    libraries = []
    for entry in CDN_DATA_DIR.iterdir():
        if entry.is_dir():
            libraries.append(entry.name)
    return {"libraries": libraries}

@app.get("/api/cdn/list/{library}")
def list_library_versions(library: str):
    lib_dir = CDN_DATA_DIR / library
    if not lib_dir.is_dir():
        raise HTTPException(status_code=404, detail="Library not found")
        
    versions = []
    for entry in lib_dir.iterdir():
        if entry.is_dir():
            versions.append(entry.name)
    return {"library": library, "versions": versions}

@app.get("/api/cdn/admin", response_class=HTMLResponse)
def cdn_admin(_: str = Depends(verify_cdn_admin)):
    manifest = get_manifest()
    rows = "\n".join(
        f"<tr><td>{path}</td><td>{entry.get('size', '')}</td><td>{entry.get('sha256', '')}</td><td>{entry.get('disabled', False)}</td></tr>"
        for path, entry in sorted(manifest.items())
    )
    return f"""<!doctype html>
<html lang="ko">
<head><meta charset="utf-8"><title>synctx CDN Admin</title></head>
<body>
<h1>CDN Admin</h1>
<table border="1" cellspacing="0" cellpadding="6">
<thead><tr><th>Path</th><th>Size</th><th>SHA-256</th><th>Disabled</th></tr></thead>
<tbody>{rows}</tbody>
</table>
</body>
</html>"""

@app.delete("/api/cdn/{path:path}")
def delete_file(path: str, user: str = Depends(verify_cdn_admin)):
    target_path = validate_path(path)
    if not target_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
        
    if target_path.is_file():
        rel_path = str(target_path.relative_to(CDN_DATA_DIR))
        target_path.unlink()
        manifest = get_manifest()
        entry = manifest.get(rel_path, {})
        entry.update({"disabled": True, "deleted_at": time.time()})
        manifest[rel_path] = entry
        save_manifest(manifest)
        log_audit("delete", rel_path, user, {})
        return {"status": "deleted", "path": path}
    raise HTTPException(status_code=400, detail="Not a file")

app.mount("/", StaticFiles(directory=str(CDN_DATA_DIR)), name="cdn")

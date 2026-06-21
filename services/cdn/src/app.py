import os
import hashlib
import json
import mimetypes
import time
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Depends, Header, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse

from synctx_common.config import load_config
from synctx_common.logging import configure_logging
from synctx_common.observability import register_observability_routes

__version__ = "0.1.0"

configure_logging(service_name="cdn")
logger = logging.getLogger(__name__)

app = FastAPI(title="synctx-cdn", description="CDN Service for synctx", version=__version__, root_path="/cdn")

config = load_config()
register_observability_routes(app, version=__version__, service_name="cdn")

MANIFEST_SCHEMA_VERSION = 1


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


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_manifest() -> dict:
    if MANIFEST_FILE.exists():
        try:
            data = json.loads(MANIFEST_FILE.read_text("utf-8"))
            if "schema_version" in data:
                return data
            return _migrate_flat_manifest(data)
        except json.JSONDecodeError as exc:
            logger.warning("manifest_parse_failed path=%s error=%s", MANIFEST_FILE, exc)
    return {"schema_version": MANIFEST_SCHEMA_VERSION, "generated_at": _now_iso(), "libraries": {}}


def _migrate_flat_manifest(flat: dict) -> dict:
    manifest = {"schema_version": MANIFEST_SCHEMA_VERSION, "generated_at": _now_iso(), "libraries": {}}
    for rel_path, entry in flat.items():
        parts = rel_path.split("/")
        if len(parts) < 3:
            continue
        library, version = parts[0], parts[1]
        filename = "/".join(parts[2:])
        libs = manifest["libraries"]
        libs.setdefault(library, {})
        libs[library].setdefault(version, {"files": {}})
        libs[library][version]["files"][filename] = {
            "sha256": entry.get("sha256", ""),
            "size": entry.get("size", 0),
            "content_type": mimetypes.guess_type(filename)[0] or "application/octet-stream",
            "uploaded_by": "unknown",
            "uploaded_at": datetime.fromtimestamp(entry.get("updated_at", 0), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "disabled": entry.get("disabled", False),
        }
    return manifest


def save_manifest(manifest: dict):
    manifest["generated_at"] = _now_iso()
    tmp_file = MANIFEST_FILE.with_suffix(".tmp")
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_file, MANIFEST_FILE)


def _set_manifest_entry(manifest: dict, library: str, version: str, filename: str, entry: dict):
    libs = manifest["libraries"]
    libs.setdefault(library, {})
    libs[library].setdefault(version, {"files": {}})
    libs[library][version]["files"][filename] = entry


def _get_manifest_entry(manifest: dict, library: str, version: str, filename: str) -> Optional[dict]:
    return manifest.get("libraries", {}).get(library, {}).get(version, {}).get("files", {}).get(filename)


def log_audit(action: str, path: str, user: str, details: dict):
    log_entry = {
        "timestamp": _now_iso(),
        "action": action,
        "path": path,
        "user": user,
        "details": details,
    }
    with open(AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry) + "\n")


def verify_cdn_admin(x_forwarded_groups: str = Header(default=""), x_forwarded_user: str = Header(default="unknown")):
    if not config.authz.enabled or not config.cdn.upload_auth.enabled:
        return x_forwarded_user
    groups = [g.strip() for g in x_forwarded_groups.split(",") if g.strip()]
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


def _parse_path_parts(rel_path: str) -> tuple[str, str, str]:
    parts = rel_path.split("/")
    return parts[0], parts[1], "/".join(parts[2:])


def validate_mime_signature(ext: str, content: bytes) -> None:
    checks = {
        ".png": lambda data: data.startswith(b"\x89PNG\r\n\x1a\n"),
        ".woff": lambda data: data.startswith(b"wOFF"),
        ".woff2": lambda data: data.startswith(b"wOF2"),
    }
    check = checks.get(ext)
    if check and not check(content[:16]):
        raise HTTPException(status_code=400, detail=f"File signature does not match extension: {ext}")


async def _process_single_upload(
    upload_file: UploadFile, target_path: Path, force: bool, user: str, manifest: dict
) -> dict:
    ext = target_path.suffix.lower()
    if ext not in config.cdn.allowed_extensions:
        raise HTTPException(status_code=400, detail=f"Extension not allowed: {ext}")

    if target_path.exists() and not force:
        raise HTTPException(status_code=409, detail=f"File already exists: {target_path.relative_to(CDN_DATA_DIR)}. Use ?force=true to overwrite.")

    content = await upload_file.read()
    file_size = len(content)
    if file_size > config.cdn.max_upload_size_bytes:
        raise HTTPException(status_code=400, detail="File too large")
    validate_mime_signature(ext, content)

    target_path.parent.mkdir(parents=True, exist_ok=True)
    sha256 = hashlib.sha256(content).hexdigest()
    target_path.write_bytes(content)

    rel_path = str(target_path.relative_to(CDN_DATA_DIR))
    library, version, filename = _parse_path_parts(rel_path)
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"

    _set_manifest_entry(manifest, library, version, filename, {
        "sha256": sha256,
        "size": file_size,
        "content_type": content_type,
        "uploaded_by": user,
        "uploaded_at": _now_iso(),
        "disabled": False,
    })

    log_audit("upload", rel_path, user, {"force": force, "sha256": sha256, "size": file_size})

    return {"path": rel_path, "sha256": sha256, "size": file_size}


@app.post("/api/cdn/upload")
async def upload(
    file: Optional[UploadFile] = File(None),
    path: Optional[str] = Form(None),
    files: Optional[List[UploadFile]] = File(None),
    base_path: Optional[str] = Form(None),
    force: bool = Query(False),
    user: str = Depends(verify_cdn_admin),
):
    manifest = get_manifest()

    if file and path:
        target = validate_path(path)
        result = await _process_single_upload(file, target, force, user, manifest)
        save_manifest(manifest)
        return {"status": "success", **result}

    if files and base_path:
        base_normalized = os.path.normpath(base_path.strip("/"))
        if base_normalized.startswith("..") or base_normalized.startswith("/"):
            raise HTTPException(status_code=400, detail="Invalid base_path")

        results = []
        for f in files:
            file_path = f"{base_normalized}/{f.filename}"
            target = validate_path(file_path)
            result = await _process_single_upload(f, target, force, user, manifest)
            results.append(result)

        save_manifest(manifest)
        return {"status": "success", "uploaded": results}

    raise HTTPException(
        status_code=400,
        detail="Provide either (file + path) for single upload or (files + base_path) for batch upload",
    )


@app.get("/api/cdn/manifest")
def get_manifest_api():
    return get_manifest()


@app.post("/api/cdn/scan")
def scan_filesystem(user: str = Depends(verify_cdn_admin)):
    manifest = get_manifest()
    added = []
    for root, _dirs, filenames in os.walk(CDN_DATA_DIR):
        for fname in filenames:
            if fname in ("manifest.json", "manifest.json.tmp", "audit.log"):
                continue
            full_path = Path(root) / fname
            rel_path = str(full_path.relative_to(CDN_DATA_DIR))
            parts = rel_path.split("/")
            if len(parts) < 3:
                continue
            library, version = parts[0], parts[1]
            filename = "/".join(parts[2:])
            existing = _get_manifest_entry(manifest, library, version, filename)
            if existing and not existing.get("disabled", False):
                continue
            content = full_path.read_bytes()
            sha256 = hashlib.sha256(content).hexdigest()
            content_type = mimetypes.guess_type(fname)[0] or "application/octet-stream"
            _set_manifest_entry(manifest, library, version, filename, {
                "sha256": sha256,
                "size": len(content),
                "content_type": content_type,
                "uploaded_by": user,
                "uploaded_at": _now_iso(),
                "disabled": False,
            })
            added.append(rel_path)
            log_audit("scan", rel_path, user, {"sha256": sha256, "size": len(content)})
    if added:
        save_manifest(manifest)
    return {"status": "success", "added": added, "count": len(added)}


@app.get("/api/cdn/list")
def list_libraries():
    manifest = get_manifest()
    return {"libraries": list(manifest.get("libraries", {}).keys())}


@app.get("/api/cdn/list/{library}")
def list_library_versions(library: str):
    manifest = get_manifest()
    lib = manifest.get("libraries", {}).get(library)
    if lib is None:
        raise HTTPException(status_code=404, detail="Library not found")
    versions = []
    for ver, ver_data in lib.items():
        file_count = len(ver_data.get("files", {}))
        versions.append({"version": ver, "file_count": file_count})
    return {"library": library, "versions": versions}


@app.get("/api/cdn/admin", response_class=HTMLResponse)
def cdn_admin():
    manifest = get_manifest()
    rows = []
    for lib_name, versions in sorted(manifest.get("libraries", {}).items()):
        for ver, ver_data in sorted(versions.items()):
            for filename, entry in sorted(ver_data.get("files", {}).items()):
                rel_path = f"{lib_name}/{ver}/{filename}"
                disabled = entry.get("disabled", False)
                size = entry.get("size", 0)
                size_display = f"{size:,}" if isinstance(size, int) else size
                delete_btn = "" if disabled else (
                    f'<button class="btn-delete" onclick="deleteFile(\'{rel_path}\')">삭제</button>'
                )
                disabled_display = '<span class="badge badge-disabled">삭제됨</span>' if disabled else ""
                rows.append(
                    f"<tr><td>{rel_path}</td><td class=\"num\">{size_display}</td>"
                    f"<td><code>{entry.get('sha256', '')[:16]}…</code></td>"
                    f"<td>{entry.get('content_type', '')}</td>"
                    f"<td>{entry.get('uploaded_by', '')}</td>"
                    f"<td>{entry.get('uploaded_at', '')}</td>"
                    f"<td>{disabled_display}</td>"
                    f"<td>{delete_btn}</td></tr>"
                )
    rows_html = "\n".join(rows)
    allowed_ext = ", ".join(config.cdn.allowed_extensions)
    max_size_mb = config.cdn.max_upload_size_bytes / 1_000_000
    return f"""<!doctype html>
<html lang="ko">
<head><meta charset="utf-8"><title>synctx CDN Admin</title>
<style>
*{{ box-sizing:border-box }}
body{{ font-family:-apple-system,sans-serif; margin:0; padding:2rem; color:#1a1a1a; background:#fafafa }}
h1{{ margin:0 0 1.5rem; font-size:1.5rem }}
h2{{ font-size:1.1rem; margin:1.5rem 0 .8rem; color:#333 }}
.layout{{ display:flex; gap:2rem; align-items:flex-start }}
.upload-panel{{ flex:0 0 360px; background:#fff; border:1px solid #ddd; border-radius:8px; padding:1.2rem }}
.file-panel{{ flex:1; min-width:0 }}
.tab-bar{{ display:flex; gap:4px; margin-bottom:1rem }}
.tab{{ padding:6px 14px; border:1px solid #ccc; border-radius:6px 6px 0 0; background:#f5f5f5; cursor:pointer; font-size:.85rem }}
.tab.active{{ background:#fff; border-bottom-color:#fff; font-weight:600 }}
.tab-content{{ display:none }} .tab-content.active{{ display:block }}
label{{ display:block; font-size:.85rem; font-weight:500; margin:0.6rem 0 .3rem; color:#555 }}
input[type=text]{{ width:100%; padding:6px 8px; border:1px solid #ccc; border-radius:4px; font-size:.9rem }}
input[type=file]{{ font-size:.85rem }}
.field-row{{ display:flex; gap:8px }}
.field-row .field{{ flex:1 }}
.checkbox-row{{ display:flex; align-items:center; gap:6px; margin:.8rem 0 }}
.checkbox-row label{{ margin:0; font-weight:normal }}
.hint{{ font-size:.75rem; color:#888; margin-top:2px }}
.btn{{ padding:8px 16px; border:none; border-radius:4px; cursor:pointer; font-size:.9rem; font-weight:500 }}
.btn-upload{{ background:#2563eb; color:#fff; margin-top:1rem; width:100% }}
.btn-upload:hover{{ background:#1d4ed8 }}
.btn-upload:disabled{{ background:#93c5fd; cursor:not-allowed }}
.btn-scan{{ background:#059669; color:#fff; margin-top:.5rem; width:100% }}
.btn-scan:hover{{ background:#047857 }}
.btn-delete{{ background:none; border:1px solid #dc2626; color:#dc2626; padding:3px 10px; border-radius:4px; cursor:pointer; font-size:.8rem }}
.btn-delete:hover{{ background:#dc2626; color:#fff }}
.badge{{ padding:2px 8px; border-radius:10px; font-size:.75rem }}
.badge-disabled{{ background:#fee2e2; color:#991b1b }}
.toast{{ position:fixed; top:1rem; right:1rem; padding:10px 18px; border-radius:6px; color:#fff; font-size:.9rem; opacity:0; transition:opacity .3s; z-index:100 }}
.toast.success{{ background:#16a34a }} .toast.error{{ background:#dc2626 }}
.toast.show{{ opacity:1 }}
.divider{{ border:0; border-top:1px solid #eee; margin:1.2rem 0 }}
table{{ border-collapse:collapse; width:100%; font-size:.85rem }}
th,td{{ border:1px solid #ddd; padding:6px 10px; text-align:left }}
th{{ background:#f0f0f0; font-weight:600; position:sticky; top:0 }}
td.num{{ text-align:right; font-variant-numeric:tabular-nums }}
.empty{{ text-align:center; color:#999; padding:2rem }}
.toolbar{{ display:flex; justify-content:space-between; align-items:center; margin-bottom:.5rem }}
.file-count{{ font-size:.8rem; color:#666 }}
</style>
</head>
<body>
<h1>synctx CDN Admin</h1>
<div class="layout">
<div class="upload-panel">
  <h2>파일 업로드</h2>
  <div class="tab-bar">
    <div class="tab active" onclick="switchTab('single')">단일 파일</div>
    <div class="tab" onclick="switchTab('batch')">다중 파일</div>
  </div>
  <div id="tab-single" class="tab-content active">
    <div class="field-row">
      <div class="field">
        <label for="s-lib">라이브러리</label>
        <input type="text" id="s-lib" placeholder="mermaid">
      </div>
      <div class="field">
        <label for="s-ver">버전</label>
        <input type="text" id="s-ver" placeholder="11.0.0">
      </div>
    </div>
    <label for="s-file">파일</label>
    <input type="file" id="s-file" onchange="onSingleFileChange()">
    <label for="s-fname">파일명</label>
    <input type="text" id="s-fname" placeholder="파일 선택 시 자동 입력">
    <div class="hint">경로: <code id="s-preview">library/version/filename</code></div>
  </div>
  <div id="tab-batch" class="tab-content">
    <div class="field-row">
      <div class="field">
        <label for="b-lib">라이브러리</label>
        <input type="text" id="b-lib" placeholder="highlight.js">
      </div>
      <div class="field">
        <label for="b-ver">버전</label>
        <input type="text" id="b-ver" placeholder="11.9.0">
      </div>
    </div>
    <label for="b-files">파일 (복수 선택 가능)</label>
    <input type="file" id="b-files" multiple>
  </div>
  <div class="checkbox-row">
    <input type="checkbox" id="force"><label for="force">기존 파일 덮어쓰기 (force)</label>
  </div>
  <div class="hint">허용 확장자: {allowed_ext}</div>
  <div class="hint">최대 크기: {max_size_mb:.0f} MB</div>
  <button class="btn btn-upload" id="btn-upload" onclick="doUpload()">업로드</button>
  <hr class="divider">
  <h2>파일시스템 스캔</h2>
  <div class="hint">CDN 저장소에 직접 복사된 파일을 manifest에 등록합니다.</div>
  <button class="btn btn-scan" id="btn-scan" onclick="doScan()">스캔</button>
</div>
<div class="file-panel">
  <div class="toolbar">
    <h2 style="margin:0">업로드된 파일</h2>
    <span class="file-count">{len(rows)}개 파일</span>
  </div>
  <table>
  <thead><tr><th>Path</th><th>Size</th><th>SHA-256</th><th>Content-Type</th><th>Uploaded By</th><th>Uploaded At</th><th>상태</th><th></th></tr></thead>
  <tbody id="file-table">{rows_html if rows_html else '<tr><td colspan="8" class="empty">업로드된 파일이 없습니다</td></tr>'}</tbody>
  </table>
</div>
</div>
<div class="toast" id="toast"></div>
<script>
function switchTab(name) {{
  document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(el => el.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  event.target.classList.add('active');
}}
function onSingleFileChange() {{
  const fileInput = document.getElementById('s-file');
  const fnameInput = document.getElementById('s-fname');
  if (fileInput.files[0]) {{
    fnameInput.value = fileInput.files[0].name;
    updatePreview();
  }}
}}
function updatePreview() {{
  const lib = document.getElementById('s-lib').value.trim() || 'library';
  const ver = document.getElementById('s-ver').value.trim() || 'version';
  const fname = document.getElementById('s-fname').value.trim() || 'filename';
  document.getElementById('s-preview').textContent = lib + '/' + ver + '/' + fname;
}}
['s-lib','s-ver','s-fname'].forEach(id => {{
  const el = document.getElementById(id);
  if (el) el.addEventListener('input', updatePreview);
}});
function toast(msg, type) {{
  const el = document.getElementById('toast');
  el.textContent = msg; el.className = 'toast ' + type + ' show';
  setTimeout(() => el.classList.remove('show'), 4000);
}}
async function doUpload() {{
  const btn = document.getElementById('btn-upload');
  btn.disabled = true; btn.textContent = '업로드 중…';
  try {{
    const force = document.getElementById('force').checked;
    const fd = new FormData();
    const isSingle = document.getElementById('tab-single').classList.contains('active');
    if (isSingle) {{
      const lib = document.getElementById('s-lib').value.trim();
      const ver = document.getElementById('s-ver').value.trim();
      const fname = document.getElementById('s-fname').value.trim();
      const file = document.getElementById('s-file').files[0];
      if (!file) {{ toast('파일을 선택하세요', 'error'); return; }}
      if (!lib) {{ toast('라이브러리명을 입력하세요', 'error'); return; }}
      if (!ver) {{ toast('버전을 입력하세요', 'error'); return; }}
      fd.append('file', file);
      fd.append('path', lib + '/' + ver + '/' + (fname || file.name));
    }} else {{
      const lib = document.getElementById('b-lib').value.trim();
      const ver = document.getElementById('b-ver').value.trim();
      const files = document.getElementById('b-files').files;
      if (!files.length) {{ toast('파일을 선택하세요', 'error'); return; }}
      if (!lib) {{ toast('라이브러리명을 입력하세요', 'error'); return; }}
      if (!ver) {{ toast('버전을 입력하세요', 'error'); return; }}
      for (const f of files) fd.append('files', f);
      fd.append('base_path', lib + '/' + ver);
    }}
    const resp = await fetch('/cdn/api/cdn/upload?force=' + force, {{ method: 'POST', body: fd }});
    const data = await resp.json();
    if (resp.ok) {{ toast('업로드 성공', 'success'); setTimeout(() => location.reload(), 500); }}
    else {{ toast(data.detail || '업로드 실패', 'error'); }}
  }} catch (e) {{ toast('오류: ' + e.message, 'error'); }}
  finally {{ btn.disabled = false; btn.textContent = '업로드'; }}
}}
async function doScan() {{
  const btn = document.getElementById('btn-scan');
  btn.disabled = true; btn.textContent = '스캔 중…';
  try {{
    const resp = await fetch('/cdn/api/cdn/scan', {{ method: 'POST' }});
    const data = await resp.json();
    if (resp.ok) {{
      if (data.count > 0) {{ toast(data.count + '개 파일 등록됨', 'success'); setTimeout(() => location.reload(), 500); }}
      else {{ toast('새로 등록할 파일이 없습니다', 'success'); }}
    }} else {{ toast(data.detail || '스캔 실패', 'error'); }}
  }} catch (e) {{ toast('오류: ' + e.message, 'error'); }}
  finally {{ btn.disabled = false; btn.textContent = '스캔'; }}
}}
async function deleteFile(path) {{
  if (!confirm(path + ' 파일을 삭제하시겠습니까?')) return;
  try {{
    const resp = await fetch('/cdn/api/cdn/' + path, {{ method: 'DELETE' }});
    const data = await resp.json();
    if (resp.ok) {{ toast('삭제 완료', 'success'); setTimeout(() => location.reload(), 500); }}
    else {{ toast(data.detail || '삭제 실패', 'error'); }}
  }} catch (e) {{ toast('오류: ' + e.message, 'error'); }}
}}
</script>
</body>
</html>"""


@app.delete("/api/cdn/{path:path}")
def delete_file(path: str, user: str = Depends(verify_cdn_admin)):
    target_path = validate_path(path)
    if not target_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    if not target_path.is_file():
        raise HTTPException(status_code=400, detail="Not a file")

    rel_path = str(target_path.relative_to(CDN_DATA_DIR))
    library, version, filename = _parse_path_parts(rel_path)

    target_path.unlink()

    manifest = get_manifest()
    entry = _get_manifest_entry(manifest, library, version, filename)
    if entry:
        entry["disabled"] = True
        entry["deleted_at"] = _now_iso()
    save_manifest(manifest)

    log_audit("delete", rel_path, user, {})
    return {"status": "deleted", "path": path}


app.mount("/", StaticFiles(directory=str(CDN_DATA_DIR)), name="cdn")

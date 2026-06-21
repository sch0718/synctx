import sys
import os
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from app import app, CDN_DATA_DIR, MANIFEST_FILE, AUDIT_LOG_FILE


@pytest.fixture(autouse=True)
def _disable_authz():
    import app as cdn_app
    original = cdn_app.config.cdn.upload_auth.enabled
    cdn_app.config.cdn.upload_auth.enabled = False
    yield
    cdn_app.config.cdn.upload_auth.enabled = original


@pytest.fixture(autouse=True)
def _use_tmp_cdn(tmp_path, monkeypatch):
    import app as cdn_app
    monkeypatch.setattr(cdn_app, "CDN_DATA_DIR", tmp_path)
    monkeypatch.setattr(cdn_app, "MANIFEST_FILE", tmp_path / "manifest.json")
    monkeypatch.setattr(cdn_app, "AUDIT_LOG_FILE", tmp_path / "audit.log")
    yield


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


class TestSingleUpload:
    def test_upload_js_file(self, client):
        resp = client.post(
            "/api/cdn/upload",
            files={"file": ("mermaid.min.js", b"var x=1;", "application/javascript")},
            data={"path": "mermaid/11.0.0/mermaid.min.js"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        assert body["path"] == "mermaid/11.0.0/mermaid.min.js"
        assert "sha256" in body

    def test_upload_css_file(self, client):
        resp = client.post(
            "/api/cdn/upload",
            files={"file": ("style.css", b"body{}", "text/css")},
            data={"path": "highlight.js/11.9.0/style.css"},
        )
        assert resp.status_code == 200

    def test_upload_png_with_valid_signature(self, client):
        png_header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        resp = client.post(
            "/api/cdn/upload",
            files={"file": ("icon.png", png_header, "image/png")},
            data={"path": "icons/1.0.0/icon.png"},
        )
        assert resp.status_code == 200

    def test_upload_png_with_invalid_signature(self, client):
        resp = client.post(
            "/api/cdn/upload",
            files={"file": ("icon.png", b"not a png", "image/png")},
            data={"path": "icons/1.0.0/icon.png"},
        )
        assert resp.status_code == 400
        assert "signature" in resp.json()["detail"].lower()

    def test_upload_disallowed_extension(self, client):
        resp = client.post(
            "/api/cdn/upload",
            files={"file": ("script.exe", b"bad", "application/octet-stream")},
            data={"path": "tools/1.0.0/script.exe"},
        )
        assert resp.status_code == 400
        assert "not allowed" in resp.json()["detail"].lower()

    def test_upload_conflict_without_force(self, client):
        data = {"path": "lib/1.0.0/a.js"}
        client.post("/api/cdn/upload", files={"file": ("a.js", b"v1", "application/javascript")}, data=data)

        resp = client.post("/api/cdn/upload", files={"file": ("a.js", b"v2", "application/javascript")}, data=data)
        assert resp.status_code == 409

    def test_upload_overwrite_with_force(self, client):
        data = {"path": "lib/1.0.0/a.js"}
        client.post("/api/cdn/upload", files={"file": ("a.js", b"v1", "application/javascript")}, data=data)

        resp = client.post(
            "/api/cdn/upload?force=true",
            files={"file": ("a.js", b"v2", "application/javascript")},
            data=data,
        )
        assert resp.status_code == 200

    def test_upload_path_traversal_blocked(self, client):
        resp = client.post(
            "/api/cdn/upload",
            files={"file": ("a.js", b"x", "application/javascript")},
            data={"path": "../../etc/passwd"},
        )
        assert resp.status_code == 400

    def test_upload_short_path_rejected(self, client):
        resp = client.post(
            "/api/cdn/upload",
            files={"file": ("a.js", b"x", "application/javascript")},
            data={"path": "lib/a.js"},
        )
        assert resp.status_code == 400
        assert "library/version/file" in resp.json()["detail"]


class TestBatchUpload:
    def test_batch_upload_multiple_files(self, client):
        resp = client.post(
            "/api/cdn/upload",
            files=[
                ("files", ("highlight.min.js", b"var h=1;", "application/javascript")),
                ("files", ("github.min.css", b"body{}", "text/css")),
            ],
            data={"base_path": "highlight.js/11.9.0/"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        assert len(body["uploaded"]) == 2
        paths = [item["path"] for item in body["uploaded"]]
        assert "highlight.js/11.9.0/highlight.min.js" in paths
        assert "highlight.js/11.9.0/github.min.css" in paths

    def test_batch_upload_invalid_base_path(self, client):
        resp = client.post(
            "/api/cdn/upload",
            files=[("files", ("a.js", b"x", "application/javascript"))],
            data={"base_path": "../../evil"},
        )
        assert resp.status_code == 400

    def test_no_file_no_files_returns_400(self, client):
        resp = client.post("/api/cdn/upload", data={"path": "lib/1.0.0/a.js"})
        assert resp.status_code in (400, 422)


class TestManifest:
    def test_manifest_api_returns_schema(self, client):
        resp = client.get("/api/cdn/manifest")
        assert resp.status_code == 200
        body = resp.json()
        assert body["schema_version"] == 1
        assert "libraries" in body

    def test_manifest_updated_after_upload(self, client):
        client.post(
            "/api/cdn/upload",
            files={"file": ("app.js", b"var a=1;", "application/javascript")},
            data={"path": "mylib/2.0.0/app.js"},
        )
        resp = client.get("/api/cdn/manifest")
        body = resp.json()
        entry = body["libraries"]["mylib"]["2.0.0"]["files"]["app.js"]
        assert entry["sha256"]
        assert entry["size"] == 8
        assert "javascript" in entry["content_type"]
        assert entry["disabled"] is False

    def test_manifest_batch_upload_structure(self, client):
        client.post(
            "/api/cdn/upload",
            files=[
                ("files", ("a.js", b"1", "application/javascript")),
                ("files", ("b.css", b"2", "text/css")),
            ],
            data={"base_path": "pkg/3.0.0"},
        )
        resp = client.get("/api/cdn/manifest")
        files = resp.json()["libraries"]["pkg"]["3.0.0"]["files"]
        assert "a.js" in files
        assert "b.css" in files


class TestListAndDelete:
    def _seed(self, client):
        client.post(
            "/api/cdn/upload",
            files={"file": ("a.js", b"1", "application/javascript")},
            data={"path": "alpha/1.0.0/a.js"},
        )
        client.post(
            "/api/cdn/upload",
            files={"file": ("b.js", b"2", "application/javascript")},
            data={"path": "beta/2.0.0/b.js"},
        )

    def test_list_libraries(self, client):
        self._seed(client)
        resp = client.get("/api/cdn/list")
        assert resp.status_code == 200
        assert set(resp.json()["libraries"]) == {"alpha", "beta"}

    def test_list_versions(self, client):
        self._seed(client)
        resp = client.get("/api/cdn/list/alpha")
        assert resp.status_code == 200
        versions = resp.json()["versions"]
        assert any(v["version"] == "1.0.0" for v in versions)

    def test_list_unknown_library_404(self, client):
        resp = client.get("/api/cdn/list/nonexistent")
        assert resp.status_code == 404

    def test_delete_file(self, client):
        self._seed(client)
        resp = client.delete("/api/cdn/alpha/1.0.0/a.js")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

        manifest = client.get("/api/cdn/manifest").json()
        entry = manifest["libraries"]["alpha"]["1.0.0"]["files"]["a.js"]
        assert entry["disabled"] is True
        assert "deleted_at" in entry

    def test_delete_nonexistent_404(self, client):
        resp = client.delete("/api/cdn/no/1.0.0/file.js")
        assert resp.status_code == 404


class TestAuthz:
    def test_upload_forbidden_without_role(self, client):
        import app as cdn_app
        cdn_app.config.authz.enabled = True
        cdn_app.config.cdn.upload_auth.enabled = True

        resp = client.post(
            "/api/cdn/upload",
            files={"file": ("a.js", b"x", "application/javascript")},
            data={"path": "lib/1.0.0/a.js"},
        )
        assert resp.status_code == 403

    def test_upload_allowed_with_role(self, client):
        import app as cdn_app
        cdn_app.config.authz.enabled = True
        cdn_app.config.cdn.upload_auth.enabled = True
        allowed = cdn_app.config.authz.role_mappings.cdn_admin
        if not allowed:
            allowed = ["cdn-admin"]
            cdn_app.config.authz.role_mappings.cdn_admin = allowed

        resp = client.post(
            "/api/cdn/upload",
            files={"file": ("a.js", b"x", "application/javascript")},
            data={"path": "lib/1.0.0/a.js"},
            headers={"X-Forwarded-Groups": allowed[0]},
        )
        assert resp.status_code == 200

    def test_upload_bypassed_when_authz_disabled(self, client):
        import app as cdn_app
        cdn_app.config.authz.enabled = False
        cdn_app.config.cdn.upload_auth.enabled = True

        resp = client.post(
            "/api/cdn/upload",
            files={"file": ("a.js", b"x", "application/javascript")},
            data={"path": "lib/1.0.0/a.js"},
        )
        assert resp.status_code == 200

class TestScanFilesystem:
    def test_scan_registers_existing_files(self, client, tmp_path):
        import app as cdn_app
        lib_dir = cdn_app.CDN_DATA_DIR / "mylib" / "1.0.0"
        lib_dir.mkdir(parents=True)
        (lib_dir / "app.js").write_bytes(b"var x=1;")

        resp = client.post("/api/cdn/scan")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert "mylib/1.0.0/app.js" in body["added"]

        manifest = client.get("/api/cdn/manifest").json()
        entry = manifest["libraries"]["mylib"]["1.0.0"]["files"]["app.js"]
        assert entry["sha256"]
        assert entry["disabled"] is False

    def test_scan_skips_already_registered(self, client, tmp_path):
        import app as cdn_app
        lib_dir = cdn_app.CDN_DATA_DIR / "mylib" / "1.0.0"
        lib_dir.mkdir(parents=True)
        (lib_dir / "app.js").write_bytes(b"var x=1;")

        client.post("/api/cdn/scan")
        resp = client.post("/api/cdn/scan")
        assert resp.json()["count"] == 0


class TestAdminUI:
    def test_admin_page_returns_html(self, client):
        client.post(
            "/api/cdn/upload",
            files={"file": ("x.js", b"1", "application/javascript")},
            data={"path": "lib/1.0.0/x.js"},
        )
        resp = client.get("/api/cdn/admin")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "lib/1.0.0/x.js" in resp.text
        assert "라이브러리" in resp.text
        assert "스캔" in resp.text


class TestAuditLog:
    def test_audit_log_written_on_upload(self, client, tmp_path):
        import app as cdn_app
        audit_file = cdn_app.AUDIT_LOG_FILE

        client.post(
            "/api/cdn/upload",
            files={"file": ("a.js", b"1", "application/javascript")},
            data={"path": "lib/1.0.0/a.js"},
        )
        assert audit_file.exists()
        lines = audit_file.read_text().strip().split("\n")
        entry = json.loads(lines[-1])
        assert entry["action"] == "upload"
        assert entry["path"] == "lib/1.0.0/a.js"

    def test_audit_log_written_on_delete(self, client, tmp_path):
        import app as cdn_app
        audit_file = cdn_app.AUDIT_LOG_FILE

        client.post(
            "/api/cdn/upload",
            files={"file": ("a.js", b"1", "application/javascript")},
            data={"path": "lib/1.0.0/a.js"},
        )
        client.delete("/api/cdn/lib/1.0.0/a.js")

        lines = audit_file.read_text().strip().split("\n")
        delete_entry = json.loads(lines[-1])
        assert delete_entry["action"] == "delete"

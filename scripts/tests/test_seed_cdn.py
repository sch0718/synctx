"""scripts/docker/seed_cdn.py 단위 테스트."""

import email.parser
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "docker"))

import seed_cdn  # noqa: E402
from synctx_common.config import AuthzConfig, AuthzRoleMappings, CdnConfig, CdnUploadAuthConfig, SynctxConfig  # noqa: E402


def test_iter_seed_files_skips_symlinks(tmp_path):
    (tmp_path / "mermaid" / "10.9.1").mkdir(parents=True)
    (tmp_path / "mermaid" / "10.9.1" / "mermaid.min.js").write_text("x")
    (tmp_path / "chart.js" / "4.4.4").mkdir(parents=True)
    (tmp_path / "chart.js" / "4.4.4" / "chart.umd.js").write_text("y")
    os.symlink(tmp_path / "mermaid" / "10.9.1", tmp_path / "mermaid" / "latest")
    os.symlink(tmp_path / "chart.js" / "4.4.4" / "chart.umd.js", tmp_path / "chart.js" / "4.4.4" / "alias.js")
    assert seed_cdn.iter_seed_files(tmp_path) == ["chart.js/4.4.4/chart.umd.js", "mermaid/10.9.1/mermaid.min.js"]


def test_admin_headers_without_authz():
    cfg = SynctxConfig(authz=AuthzConfig(enabled=False))
    assert seed_cdn.admin_headers(cfg) == {"X-Forwarded-User": "deploy-seed"}


def test_admin_headers_prefers_cdn_admin_group():
    cfg = SynctxConfig(authz=AuthzConfig(enabled=True, role_mappings=AuthzRoleMappings(cdn_admin=["cdn-team"], platform_admin=["ops"])))
    assert seed_cdn.admin_headers(cfg)["X-Forwarded-Groups"] == "cdn-team"


def test_admin_headers_falls_back_to_platform_admin():
    cfg = SynctxConfig(authz=AuthzConfig(enabled=True, role_mappings=AuthzRoleMappings(platform_admin=["ops"])))
    assert seed_cdn.admin_headers(cfg)["X-Forwarded-Groups"] == "ops"


def test_admin_headers_without_group_fails():
    cfg = SynctxConfig(authz=AuthzConfig(enabled=True))
    with pytest.raises(SystemExit):
        seed_cdn.admin_headers(cfg)


def test_admin_headers_when_upload_auth_disabled():
    cfg = SynctxConfig(authz=AuthzConfig(enabled=True), cdn=CdnConfig(upload_auth=CdnUploadAuthConfig(enabled=False)))
    assert "X-Forwarded-Groups" not in seed_cdn.admin_headers(cfg)


def test_build_multipart_is_parseable():
    body = seed_cdn.build_multipart("mermaid/10.9.1/mermaid.min.js", b"\x00js", "BOUNDARY")
    message = email.parser.BytesParser().parsebytes(
        b"Content-Type: multipart/form-data; boundary=BOUNDARY\r\n\r\n" + body
    )
    parts = {part.get_param("name", header="content-disposition"): part for part in message.get_payload()}
    assert parts["path"].get_payload(decode=True) == b"mermaid/10.9.1/mermaid.min.js"
    assert parts["file"].get_filename() == "mermaid.min.js"
    assert parts["file"].get_payload(decode=True) == b"\x00js"


@pytest.mark.parametrize(
    "status,body,expected",
    [(200, b"same", None), (200, b"old", True), (404, b"", False)],
)
def test_upload_decision(status, body, expected):
    assert seed_cdn.upload_decision(status, body, b"same") is expected


def test_upload_decision_rejects_unexpected_status():
    with pytest.raises(RuntimeError):
        seed_cdn.upload_decision(500, b"", b"x")


def test_main_fails_when_seed_dir_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(seed_cdn, "SEED_DIR", tmp_path / "missing")
    assert seed_cdn.main() == 1
    assert "CDN 채우기 대상 디렉터리가 없습니다" in capsys.readouterr().err


def test_main_fails_when_seed_dir_empty(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(seed_cdn, "SEED_DIR", tmp_path)
    assert seed_cdn.main() == 1
    assert "CDN 채우기 대상 파일이 없습니다" in capsys.readouterr().err

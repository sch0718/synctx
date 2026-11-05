"""내보내기용 런타임 설정 생성과 Viewer 앱 로드."""
from __future__ import annotations

import hashlib
import importlib
import os
import tempfile
from pathlib import Path

import yaml

from static_export.config import SiteConfig


def write_runtime_config(site: SiteConfig, cache_dir: Path) -> Path:
    """운영 `.synctx.yaml` 모양의 설정을 만든다. include·exclude는 옮기지 않는다(제한은 크롤 허용 목록이 맡는다)."""
    from synctx_common.config import SynctxConfig

    cache = Path(cache_dir).expanduser().resolve()
    cache.mkdir(parents=True, exist_ok=True)
    repositories = []
    for repo in site.repositories:
        auth = {"type": "token", "credential_env": repo.auth["credential_env"]} if repo.auth.get("credential_env") else {"type": "none"}
        repositories.append({"id": repo.id, "name": repo.name, "url": repo.url, "default_ref": repo.default_ref, "auth": auth})
    data = {
        "authz": {"enabled": False},
        "runtime": {"public_base_url": "http://127.0.0.1"},
        "viewer": {"asset_source": "bundled", "repo_cache_path": str(cache / "repos")},
        "collab": {"jitsi": {"enabled": False}},
        "repositories": repositories,
    }
    SynctxConfig.model_validate(data)
    serialized = yaml.safe_dump(data, allow_unicode=True, sort_keys=True)
    target = cache / f"static-export.{hashlib.sha256(serialized.encode('utf-8')).hexdigest()[:16]}.yaml"
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=cache, delete=False) as output:
        temporary = Path(output.name)
        try:
            output.write(serialized)
            output.close()
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    return target


def load_viewer(config_path: Path):
    """설정 경로를 환경변수로 넘기고 Viewer 앱 모듈을 불러온다(앱은 import 때 설정을 읽는다)."""
    os.environ["SYNCTX_CONFIG_PATH"] = str(config_path)
    os.environ["SYNCTX_ENABLED_SERVICES"] = "viewer"
    return importlib.import_module("standalone")._load_viewer()

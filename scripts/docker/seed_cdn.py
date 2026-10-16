"""cdn 컨테이너 안에서 /seed의 vendor 파일을 업로드 API로 적재한다.

deploy.sh가 `docker compose exec -T cdn python -I - < seed_cdn.py`로 실행한다.
업로드 API를 거쳐 manifest·audit 기록을 남기고, 내용이 같으면 건너뛰고 다르면 덮어쓴다.
"""

from __future__ import annotations

import hashlib
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

BASE_URL = os.getenv("CDN_SEED_BASE_URL", "http://127.0.0.1:8000")
SEED_DIR = Path(os.getenv("CDN_SEED_DIR", "/seed"))


def iter_seed_files(root: Path) -> list[str]:
    # latest/ 같은 심볼릭 링크는 운영 금지 alias라 올리지 않는다(K8s 시딩과 같은 기준)
    files = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            path = Path(dirpath) / name
            if path.is_symlink() or not path.is_file():
                continue
            files.append(path.relative_to(root).as_posix())
    return sorted(files)


def admin_headers(cfg) -> dict[str, str]:
    headers = {"X-Forwarded-User": "deploy-seed"}
    if cfg.authz.enabled and cfg.cdn.upload_auth.enabled:
        groups = cfg.authz.role_mappings.cdn_admin or cfg.authz.role_mappings.platform_admin
        if not groups:
            raise SystemExit("authz.role_mappings에 cdn-admin 또는 platform-admin 그룹이 없어 CDN에 올릴 수 없습니다")
        headers["X-Forwarded-Groups"] = groups[0]
    return headers


def build_multipart(rel: str, content: bytes, boundary: str) -> bytes:
    filename = rel.rsplit("/", 1)[-1].replace('"', "%22")
    return b"".join([
        f'--{boundary}\r\nContent-Disposition: form-data; name="path"\r\n\r\n{rel}\r\n'.encode("utf-8"),
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8") + content + b"\r\n",
        f"--{boundary}--\r\n".encode("utf-8"),
    ])


def upload_decision(remote_status: int, remote_body: bytes, local: bytes) -> bool | None:
    if remote_status == 200:
        if hashlib.sha256(remote_body).digest() == hashlib.sha256(local).digest():
            return None
        return True
    if remote_status == 404:
        return False
    raise RuntimeError(f"CDN 조회가 예상하지 못한 상태 코드를 돌려줬습니다: {remote_status}")


def _fetch(rel: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(f"{BASE_URL}/{urllib.parse.quote(rel)}", timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, b""


def _upload(rel: str, content: bytes, force: bool, headers: dict[str, str]) -> None:
    boundary = uuid.uuid4().hex
    request = urllib.request.Request(
        f"{BASE_URL}/api/cdn/upload?force={'true' if force else 'false'}",
        data=build_multipart(rel, content, boundary),
        headers={**headers, "Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as resp:
        resp.read()


def main() -> int:
    from synctx_common.config import load_config

    # 잘못 마운트된 /seed를 성공으로 넘기면 CDN이 비어 있는 채로 배포가 끝나므로 실패로 끝낸다
    if not SEED_DIR.is_dir():
        print(f"CDN 채우기 대상 디렉터리가 없습니다: {SEED_DIR}", file=sys.stderr)
        return 1
    files = iter_seed_files(SEED_DIR)
    if not files:
        print(f"CDN 채우기 대상 파일이 없습니다: {SEED_DIR}", file=sys.stderr)
        return 1

    headers = admin_headers(load_config())
    uploaded = skipped = 0
    for rel in files:
        content = (SEED_DIR / rel).read_bytes()
        status, body = _fetch(rel)
        force = upload_decision(status, body, content)
        if force is None:
            skipped += 1
            continue
        try:
            _upload(rel, content, force, headers)
        except urllib.error.HTTPError as e:
            print(f"CDN 업로드 실패: {rel} ({e.code})", file=sys.stderr)
            return 1
        print(f"업로드: {rel}{' (덮어쓰기)' if force else ''}")
        uploaded += 1
    print(f"CDN 채우기 완료 (업로드 {uploaded}, 동일 {skipped})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

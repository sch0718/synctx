"""XWiki API Client."""

import logging
from typing import Any, Dict, Optional

import httpx
from synctx_common.config import XWikiTargetConfig

logger = logging.getLogger(__name__)

class XWikiClient:
    def __init__(self, config: XWikiTargetConfig):
        self.config = config
        self.base_url = config.base_url.rstrip("/")
        
        # Setup auth
        auth = None
        self.user = ""
        if config.auth.type == "basic":
            import os
            cred = os.getenv(config.auth.credential_env)
            if cred and ":" in cred:
                user, pwd = cred.split(":", 1)
                auth = (user, pwd)
                self.user = user
            else:
                logger.warning(f"Invalid or missing XWiki basic auth in env {config.auth.credential_env}")
                
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            auth=auth,
            timeout=config.timeout_seconds
        )

    async def close(self):
        await self.client.aclose()

    async def get_page(self, space: str, page: str) -> Optional[Dict[str, Any]]:
        """페이지 조회. 없으면 None 반환."""
        url = f"/rest/wikis/{self.config.wiki}/spaces/{space}/pages/{page}"
        response = await self.client.get(url, headers={"Accept": "application/json"})
        
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def upsert_page(self, space: str, page: str, title: str, content: str) -> bool:
        """페이지 생성 또는 갱신 (Idempotent)."""
        url = f"/rest/wikis/{self.config.wiki}/spaces/{space}/pages/{page}"
        
        # 1. 존재하는지 확인
        existing = await self.get_page(space, page)
        
        payload = {
            "title": title,
            "content": content,
            "syntax": self.config.syntax
        }
        
        if existing:
            # 내용이 동일한지 확인 (간단한 비교)
            if existing.get("title") == title and existing.get("content") == content:
                logger.debug(f"XWiki page {space}/{page} is up to date.")
                return False # No change made
                
            # Update
            response = await self.client.put(
                url, 
                json=payload,
                headers={"Content-Type": "application/json", "Accept": "application/json"}
            )
            response.raise_for_status()
            logger.info(f"Updated XWiki page {space}/{page}")
            return True
        else:
            # Create
            response = await self.client.put(
                url, 
                json=payload,
                headers={"Content-Type": "application/json", "Accept": "application/json"}
            )
            response.raise_for_status()
            logger.info(f"Created XWiki page {space}/{page}")
            return True

    async def delete_page(self, space: str, page: str) -> bool:
        """페이지 삭제."""
        url = f"/rest/wikis/{self.config.wiki}/spaces/{space}/pages/{page}"
        response = await self.client.delete(url)
        if response.status_code == 404:
            return False
        response.raise_for_status()
        logger.info(f"Deleted XWiki page {space}/{page}")
        return True

    async def get_attachment(self, space: str, page: str, filename: str) -> Optional[Dict[str, Any]]:
        """첨부파일 메타데이터 조회."""
        url = f"/rest/wikis/{self.config.wiki}/spaces/{space}/pages/{page}/attachments/{filename}"
        response = await self.client.get(url, headers={"Accept": "application/json"})
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def upload_attachment(self, space: str, page: str, filename: str, content: bytes, content_type: str = "application/octet-stream") -> bool:
        """첨부파일 업로드 (SHA/크기 비교를 통한 skip 로직 포함)."""
        import hashlib
        
        existing = await self.get_attachment(space, page, filename)
        if existing:
            # XWiki API doesn't expose SHA directly in standard REST attachment response usually,
            # but we can check size. If size matches, we might skip or we can download and check SHA.
            # For now, if size matches exactly, we skip to save bandwidth, or we can force upload.
            # Let's implement a strict SHA check by downloading if size matches.
            size = existing.get("size", 0)
            if size == len(content):
                # Download existing to check SHA
                download_url = f"/rest/wikis/{self.config.wiki}/spaces/{space}/pages/{page}/attachments/{filename}/bytes"
                dl_resp = await self.client.get(download_url)
                if dl_resp.status_code == 200:
                    existing_sha = hashlib.sha256(dl_resp.content).hexdigest()
                    new_sha = hashlib.sha256(content).hexdigest()
                    if existing_sha == new_sha:
                        logger.debug(f"Attachment {filename} already up to date (SHA matched).")
                        return False

        url = f"/rest/wikis/{self.config.wiki}/spaces/{space}/pages/{page}/attachments"
        files = {
            "file": (filename, content, content_type)
        }
        # XWiki expects POST for attachment creation/update
        response = await self.client.post(url, files=files, headers={"Accept": "application/json"})
        response.raise_for_status()
        logger.info(f"Uploaded attachment {filename} to {space}/{page}")
        return True

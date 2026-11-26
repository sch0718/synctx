"""OpenProject API Client."""

import logging
from typing import Any, Dict, Optional

import httpx
from synctx_common.config import OpenProjectTargetConfig
from sync_errors import SyncError

logger = logging.getLogger(__name__)

class OpenProjectClient:
    def __init__(self, config: OpenProjectTargetConfig):
        self.config = config
        self.base_url = config.base_url.rstrip("/")
        
        # Setup auth
        import os
        token = os.getenv(config.auth.credential_env)
        if not token:
            logger.warning(f"Missing OpenProject token in env {config.auth.credential_env}")
            
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            auth=("apikey", token) if token else None,
            timeout=30.0
        )

    async def close(self):
        await self.client.aclose()

    async def get_work_package_by_external_key(self, external_key: str) -> Optional[Dict[str, Any]]:
        """external_key custom field로 Work Package 조회."""
        url = "/api/v3/work_packages"
        params = {"filters": f'[{{"{self.config.external_key_custom_field}":{{"operator":"=","values":["{external_key}"]}}}}]'}
        
        response = await self.client.get(url, params=params)
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError as e:
            raise SyncError("Invalid JSON in OpenProject lookup response", "transient_error") from e
        embedded = data.get("_embedded") if isinstance(data, dict) else None
        elements = embedded.get("elements") if isinstance(embedded, dict) else None
        if not isinstance(elements, list) or any(not isinstance(item, dict) for item in elements):
            raise SyncError("Invalid elements in OpenProject lookup response", "transient_error")
        if len(elements) > 1:
            raise SyncError(f"Duplicate OpenProject external key: {external_key}", "conflict_error")
        return elements[0] if elements else None

    async def get_version_by_name(self, project_id: str, version_name: str) -> Optional[int]:
        """프로젝트 내에서 이름으로 Version ID 조회."""
        url = f"/api/v3/projects/{project_id}/versions"
        try:
            response = await self.client.get(url)
            response.raise_for_status()
            data = response.json()
            elements = data.get("_embedded", {}).get("elements", [])
            for v in elements:
                if v.get("name") == version_name:
                    return v.get("id")
        except Exception as e:
            logger.error(f"Failed to query OpenProject version {version_name}: {e}")
        return None

    def _apply_protected_fields(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """기존 WP를 갱신할 때 생성 전용 필드를 제외한다."""
        filtered = dict(payload)
        links = dict(filtered.pop("_links", {}))
        for field in self.config.protected_fields:
            filtered.pop(field, None)
            links.pop(field, None)
        if links:
            filtered["_links"] = links
        return filtered

    async def mark_deleted(self, project_id: str, external_key: str) -> bool:
        """삭제된 파일에 대해 on_delete 정책에 따라 처리."""
        existing = await self.get_work_package_by_external_key(external_key)
        if existing is None:
            return False

        closed_status_id = self.config.status_map.get("closed")
        if closed_status_id is None:
            closed_status_id = self.config.status_map.get("done")
        if closed_status_id is None:
            logger.error("No 'closed' or 'done' status_id in status_map — cannot mark deleted")
            raise ValueError("status_map missing 'closed' or 'done' entry for mark_deleted")

        wp_id = existing["id"]
        payload = {
            "lockVersion": existing.get("lockVersion", 0),
            "_links": {
                "status": {"href": f"/api/v3/statuses/{closed_status_id}"},
            },
        }
        try:
            response = await self.client.patch(f"/api/v3/work_packages/{wp_id}", json=payload)
            response.raise_for_status()
            logger.info(f"Marked Work Package {wp_id} as closed (status={closed_status_id}) for {external_key}")
            return True
        except Exception as e:
            logger.error(f"Failed to mark WP {wp_id} as deleted: {e}")
            raise

    async def upsert_work_package(self, project_id: str, external_key: str, payload: Dict[str, Any]) -> bool:
        """Work Package 생성 또는 갱신 (Idempotent)."""
        existing = await self.get_work_package_by_external_key(external_key)
        
        # payload에 external_key 세팅
        payload[self.config.external_key_custom_field] = external_key
        
        if existing is not None:
            wp_id = existing["id"]
            url = f"/api/v3/work_packages/{wp_id}"
            
            payload["lockVersion"] = existing.get("lockVersion", 0)
            
            payload = self._apply_protected_fields(payload)
            
            response = await self.client.patch(url, json=payload)
            response.raise_for_status()
            logger.info(f"Updated OpenProject Work Package {wp_id} for {external_key}")
            return True
        else:
            url = f"/api/v3/projects/{project_id}/work_packages"
            response = await self.client.post(url, json=payload)
            response.raise_for_status()
            logger.info(f"Created OpenProject Work Package for {external_key}")
            return True

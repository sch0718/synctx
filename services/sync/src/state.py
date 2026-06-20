"""Sync Engine 상태 파일 관리."""

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

class StateManager:
    def __init__(self, state_dir: str):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def _get_path(self, repo_id: str) -> Path:
        return self.state_dir / f"{repo_id}.json"

    def get_state(self, repo_id: str) -> Dict[str, Any]:
        path = self._get_path(repo_id)
        bak_path = path.with_suffix(".bak")
        
        for p in [path, bak_path]:
            if p.exists():
                try:
                    return json.loads(p.read_text("utf-8"))
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse state file {p} for {repo_id}: {e}")
                    
        return {"last_successful_sha": None}

    def save_state(self, repo_id: str, state: Dict[str, Any]) -> None:
        import os
        path = self._get_path(repo_id)
        temp_path = path.with_suffix(".tmp")
        bak_path = path.with_suffix(".bak")
        
        # Write to temp file and fsync
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
            
        # Backup existing
        if path.exists():
            path.replace(bak_path)
            
        # Replace with new
        temp_path.replace(path)
        
        # Directory fsync
        if hasattr(os, "O_DIRECTORY"):
            try:
                fd = os.open(str(self.state_dir), os.O_RDONLY | os.O_DIRECTORY)
                os.fsync(fd)
                os.close(fd)
            except Exception as e:
                logger.warning(f"Directory fsync failed: {e}")

    def get_last_successful_sha(self, repo_id: str) -> Optional[str]:
        return self.get_state(repo_id).get("last_successful_sha")

    def update_last_successful_sha(self, repo_id: str, sha: str) -> None:
        state = self.get_state(repo_id)
        state["last_successful_sha"] = sha
        self.save_state(repo_id, state)

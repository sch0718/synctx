"""Sync Engine 상태 파일 관리 — per-file 추적 포함."""

import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1


@dataclass
class SyncedFileState:
    content_sha: str
    target: str
    external_key: str
    resource_id: str = ""
    last_synced_sha: str = ""
    last_synced_at: str = ""
    # XWiki에 게시한 계정. ""=기본 계정, 그 외=전용 계정 사용자 이름, None=기록 없음(모름).
    publisher: Optional[str] = ""


@dataclass
class PendingFailure:
    path: str
    error_type: str
    message: str
    attempt_count: int = 1
    first_failed_at: str = ""


@dataclass
class RepoState:
    schema_version: int = SCHEMA_VERSION
    repo_id: str = ""
    last_successful_sha: Optional[str] = None
    last_run_id: str = ""
    synced_files: Dict[str, SyncedFileState] = field(default_factory=dict)
    pending_failures: List[PendingFailure] = field(default_factory=list)
    html_dependencies: Optional[Dict[str, List[str]]] = None
    html_policy_fingerprint: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "schema_version": self.schema_version,
            "repo_id": self.repo_id,
            "last_successful_sha": self.last_successful_sha,
            "last_run_id": self.last_run_id,
            "synced_files": {
                path: asdict(sf) for path, sf in self.synced_files.items()
            },
            "pending_failures": [asdict(pf) for pf in self.pending_failures],
            "html_dependencies": self.html_dependencies,
            "html_policy_fingerprint": self.html_policy_fingerprint,
        }
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RepoState":
        state = cls(
            schema_version=data.get("schema_version", SCHEMA_VERSION),
            repo_id=data.get("repo_id", ""),
            last_successful_sha=data.get("last_successful_sha"),
            last_run_id=data.get("last_run_id", ""),
            html_dependencies=data.get("html_dependencies"),
            html_policy_fingerprint=data.get("html_policy_fingerprint", ""),
        )
        # 게시자 기록이 없는 항목: HTML 임베드 도입 전 상태는 기본 계정만 있었으므로 "",
        # 도입 후 상태(html_dependencies 있음)는 전용 계정이 게시했을 수 있으므로 모름(None).
        unknown = None if "html_dependencies" in data else ""
        for path, sf_data in data.get("synced_files", {}).items():
            state.synced_files[path] = SyncedFileState(**{"publisher": unknown, **sf_data})
        for pf_data in data.get("pending_failures", []):
            state.pending_failures.append(PendingFailure(**pf_data))
        return state


class StateManager:
    def __init__(self, state_dir: str):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def _get_path(self, repo_id: str) -> Path:
        return self.state_dir / f"{repo_id}.json"

    def get_state(self, repo_id: str) -> RepoState:
        path = self._get_path(repo_id)
        bak_path = path.with_suffix(".bak")

        for p in [path, bak_path]:
            if p.exists():
                try:
                    data = json.loads(p.read_text("utf-8"))
                    if p == bak_path:
                        logger.warning(f"state_recovered_from_backup: {repo_id}")
                    return self._migrate(data, repo_id)
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse state file {p} for {repo_id}: {e}")

        return RepoState(repo_id=repo_id)

    def _migrate(self, data: Dict[str, Any], repo_id: str) -> RepoState:
        """기존 flat state에서 마이그레이션."""
        if "schema_version" not in data:
            state = RepoState(
                repo_id=repo_id,
                last_successful_sha=data.get("last_successful_sha"),
            )
            return state
        return RepoState.from_dict(data)

    def save_state(self, repo_id: str, state: RepoState) -> None:
        path = self._get_path(repo_id)
        temp_path = path.with_suffix(f".tmp.{os.getpid()}")
        bak_path = path.with_suffix(".bak")

        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(state.to_dict(), f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())

        if path.exists():
            path.replace(bak_path)

        temp_path.replace(path)

        if hasattr(os, "O_DIRECTORY"):
            try:
                fd = os.open(str(self.state_dir), os.O_RDONLY | os.O_DIRECTORY)
                os.fsync(fd)
                os.close(fd)
            except Exception as e:
                logger.warning(f"Directory fsync failed: {e}")

    def get_last_successful_sha(self, repo_id: str) -> Optional[str]:
        return self.get_state(repo_id).last_successful_sha

    def update_last_successful_sha(self, repo_id: str, sha: str) -> None:
        state = self.get_state(repo_id)
        state.last_successful_sha = sha
        self.save_state(repo_id, state)

    def is_file_changed(self, repo_id: str, path: str, content_sha: str) -> bool:
        """파일의 content SHA가 마지막 동기화 시점과 달라졌는지 확인."""
        state = self.get_state(repo_id)
        if path not in state.synced_files:
            return True
        return state.synced_files[path].content_sha != content_sha

    def record_file_synced(
        self, state: RepoState, path: str, content_sha: str, target: str,
        external_key: str, resource_id: str, commit_sha: str, synced_at: str,
        publisher: Optional[str] = "",
    ) -> None:
        state.synced_files[path] = SyncedFileState(
            content_sha=content_sha,
            target=target,
            external_key=external_key,
            resource_id=resource_id,
            last_synced_sha=commit_sha,
            last_synced_at=synced_at,
            publisher=publisher,
        )

    def record_file_deleted(self, state: RepoState, path: str) -> None:
        state.synced_files.pop(path, None)

    def add_pending_failure(self, state: RepoState, path: str, error_type: str, message: str) -> None:
        for pf in state.pending_failures:
            if pf.path == path:
                pf.error_type = error_type
                pf.message = message
                pf.attempt_count += 1
                return
        state.pending_failures.append(PendingFailure(
            path=path,
            error_type=error_type,
            message=message,
            first_failed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        ))

    def clear_pending_failure(self, state: RepoState, path: str) -> None:
        state.pending_failures = [pf for pf in state.pending_failures if pf.path != path]

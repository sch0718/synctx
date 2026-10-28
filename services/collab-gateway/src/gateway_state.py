"""알림 상태 파일: 회의별로 마지막 상태와 이미 보낸 알림 키를 PVC의 JSON 한 파일에 둔다."""

from __future__ import annotations

import fcntl
import json
import logging
import os
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

STATE_VERSION = 1
PRUNE_AFTER = timedelta(days=90)


class StateStore:
    FILE_NAME = "collab_gateway_state.json"
    LOCK_NAME = "collab_gateway_state.lock"

    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / self.FILE_NAME
        self._lock_fd = None
        self._meetings: dict[str, dict] = self._load()

    def _load(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("version") != STATE_VERSION or not isinstance(data.get("meetings"), dict):
                raise ValueError("unsupported state")
            return data["meetings"]
        except (OSError, ValueError):
            aside = self.path.with_name(f"{self.FILE_NAME}.corrupt-{int(time.time())}")
            try:
                self.path.rename(aside)
            except OSError:
                pass
            logger.error("collab_state_reset: 상태 파일을 읽지 못해 %s로 옮기고 새로 시작합니다", aside.name)
            return {}

    def get(self, key: str) -> dict | None:
        return self._meetings.get(key)

    def put(self, key: str, entry: dict, now: datetime) -> None:
        entry["last_seen"] = now.isoformat()
        self._meetings[key] = entry

    def prune(self, now: datetime) -> None:
        cutoff = now - PRUNE_AFTER
        for key in list(self._meetings):
            try:
                seen = datetime.fromisoformat(self._meetings[key]["last_seen"])
            except (KeyError, ValueError, TypeError):
                continue
            if seen < cutoff:
                del self._meetings[key]

    def save(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"version": STATE_VERSION, "meetings": self._meetings}, ensure_ascii=False)
        fd, tmp = tempfile.mkstemp(dir=self.directory, prefix=self.FILE_NAME + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def acquire(self) -> bool:
        """다른 프로세스가 같은 상태 파일로 돌고 있으면 False."""
        self.directory.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.directory / self.LOCK_NAME, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self._lock_fd = fd
        return True

    def release(self) -> None:
        if self._lock_fd is not None:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            os.close(self._lock_fd)
            self._lock_fd = None

"""Outbound feedback event persistence."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class EventStore:
    def __init__(self, path: str | None = None):
        self.path = Path(path or os.getenv("OUTBOUND_FEEDBACK_EVENT_STORE", "/data/sync-state/feedback_events.json"))

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"events": {}}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"events": {}}

    def save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, self.path)

    def has_event(self, event_id: str) -> bool:
        return event_id in self.load().get("events", {})

    def record(self, event_id: str, payload: dict[str, Any], message: dict[str, Any], status: str) -> None:
        data = self.load()
        data.setdefault("events", {})[event_id] = {
            "status": status,
            "payload": payload,
            "message": message,
        }
        self.save(data)

    def get(self, event_id: str) -> dict[str, Any] | None:
        return self.load().get("events", {}).get(event_id)

    def list(self, repo_id: str | None = None, since: str | None = None) -> list[dict[str, Any]]:
        events = []
        for event_id, event in self.load().get("events", {}).items():
            payload = event.get("payload", {})
            if repo_id and not str(payload.get("external_key", "")).startswith(f"{repo_id}:"):
                continue
            if since and str(payload.get("timestamp", "")) < since:
                continue
            events.append({"event_id": event_id, **event})
        return sorted(events, key=lambda item: item.get("payload", {}).get("timestamp", ""), reverse=True)

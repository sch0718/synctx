import json
import os
import stat
from datetime import datetime, timedelta, timezone

import pytest

from state import StateStore

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)


def test_missing_file_gives_empty_state(tmp_path):
    store = StateStore(tmp_path)
    assert store.get("r/m1") is None


def test_roundtrip_persists_across_instances(tmp_path):
    store = StateStore(tmp_path)
    store.put("r/m1", {"snapshot": {"title": "t"}, "sent": {"k": "v"}}, NOW)
    store.save()
    again = StateStore(tmp_path)
    assert again.get("r/m1")["sent"] == {"k": "v"}
    assert again.get("r/m1")["last_seen"] == NOW.isoformat()


def test_save_is_atomic_and_leaves_no_temp_files(tmp_path):
    store = StateStore(tmp_path)
    store.put("r/m1", {"sent": {}}, NOW)
    store.save()
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []
    json.loads((tmp_path / StateStore.FILE_NAME).read_text())


def test_state_file_not_world_readable(tmp_path):
    store = StateStore(tmp_path)
    store.put("r/m1", {"sent": {}}, NOW)
    store.save()
    mode = stat.S_IMODE(os.stat(tmp_path / StateStore.FILE_NAME).st_mode)
    assert mode & 0o077 == 0


def test_corrupt_file_is_set_aside_and_state_resets(tmp_path):
    (tmp_path / StateStore.FILE_NAME).write_text("{not json")
    store = StateStore(tmp_path)
    assert store.get("r/m1") is None
    assert any(p.name.startswith(StateStore.FILE_NAME + ".corrupt") for p in tmp_path.iterdir())


def test_unknown_version_is_set_aside(tmp_path):
    (tmp_path / StateStore.FILE_NAME).write_text(json.dumps({"version": 99, "meetings": {"a": {}}}))
    assert StateStore(tmp_path).get("a") is None


def test_prune_removes_entries_not_seen_for_90_days(tmp_path):
    store = StateStore(tmp_path)
    store.put("old", {"sent": {}}, NOW - timedelta(days=91))
    store.put("new", {"sent": {}}, NOW - timedelta(days=89))
    store.prune(NOW)
    assert store.get("old") is None and store.get("new") is not None


def test_lock_is_exclusive(tmp_path):
    first = StateStore(tmp_path)
    second = StateStore(tmp_path)
    assert first.acquire() is True
    assert second.acquire() is False
    first.release()
    assert second.acquire() is True
    second.release()


def test_creates_missing_directory(tmp_path):
    store = StateStore(tmp_path / "nested" / "dir")
    store.put("a", {"sent": {}}, NOW)
    store.save()
    assert (tmp_path / "nested" / "dir" / StateStore.FILE_NAME).exists()


def test_save_without_changes_still_valid(tmp_path):
    store = StateStore(tmp_path)
    store.save()
    assert StateStore(tmp_path).get("x") is None

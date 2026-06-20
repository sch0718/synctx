import json

import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from event_store import EventStore


@pytest.fixture
def store(tmp_path):
    return EventStore(path=str(tmp_path / "events.json"))


class TestEventStore:
    def test_empty_store(self, store):
        assert store.has_event("nonexistent") is False
        assert store.get("nonexistent") is None
        assert store.list() == []

    def test_record_and_get(self, store):
        store.record("evt_1", {"key": "value"}, {"subject": "test"}, "sent")
        assert store.has_event("evt_1") is True
        event = store.get("evt_1")
        assert event["status"] == "sent"
        assert event["payload"]["key"] == "value"

    def test_list_returns_all(self, store):
        store.record("evt_1", {"timestamp": "2026-01-01"}, {}, "sent")
        store.record("evt_2", {"timestamp": "2026-01-02"}, {}, "sent")
        events = store.list()
        assert len(events) == 2
        assert events[0]["event_id"] == "evt_2"

    def test_list_filter_by_since(self, store):
        store.record("evt_old", {"timestamp": "2026-01-01"}, {}, "sent")
        store.record("evt_new", {"timestamp": "2026-06-01"}, {}, "sent")
        events = store.list(since="2026-03-01")
        assert len(events) == 1
        assert events[0]["event_id"] == "evt_new"

    def test_list_filter_by_repo_id(self, store):
        store.record("evt_a", {"external_key": "repo-a:task.md", "timestamp": "2026-01-01"}, {}, "sent")
        store.record("evt_b", {"external_key": "repo-b:task.md", "timestamp": "2026-01-01"}, {}, "sent")
        events = store.list(repo_id="repo-a")
        assert len(events) == 1
        assert events[0]["event_id"] == "evt_a"

    def test_persistence(self, tmp_path):
        path = str(tmp_path / "events.json")
        store1 = EventStore(path=path)
        store1.record("evt_1", {"a": 1}, {}, "sent")

        store2 = EventStore(path=path)
        assert store2.has_event("evt_1") is True

    def test_corrupt_file_recovers(self, tmp_path):
        path = tmp_path / "events.json"
        path.write_text("not valid json")
        store = EventStore(path=str(path))
        assert store.list() == []

    def test_duplicate_event_overwrites(self, store):
        store.record("evt_1", {"a": 1}, {}, "pending")
        store.record("evt_1", {"a": 2}, {}, "sent")
        event = store.get("evt_1")
        assert event["status"] == "sent"
        assert event["payload"]["a"] == 2

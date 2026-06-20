import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from redactor import _extract_external_key, _hash_user, _stable_event_id, redact_work_package
from synctx_common.config import (
    SynctxConfig, OpenProjectTargetConfig, OutboundFeedbackConfig,
    AuthzConfig, TargetsConfig, TargetAuthConfig,
)


def _make_config(**overrides):
    defaults = {
        "targets": TargetsConfig(
            openproject=OpenProjectTargetConfig(
                base_url="https://op.internal",
                auth=TargetAuthConfig(type="token", credential_env="OP_TOKEN"),
                external_key_custom_field="customField1",
            )
        ),
        "outbound_feedback": OutboundFeedbackConfig(
            enabled=True,
            allowed_fields=["status", "priority", "subject"],
        ),
        "authz": AuthzConfig(),
    }
    defaults.update(overrides)
    return SynctxConfig(**defaults)


class TestStableEventId:
    def test_deterministic(self):
        wp = {"id": 42, "updatedAt": "2026-01-01T00:00:00Z"}
        assert _stable_event_id(wp) == _stable_event_id(wp)

    def test_different_for_different_updates(self):
        wp1 = {"id": 42, "updatedAt": "2026-01-01T00:00:00Z"}
        wp2 = {"id": 42, "updatedAt": "2026-01-02T00:00:00Z"}
        assert _stable_event_id(wp1) != _stable_event_id(wp2)

    def test_format(self):
        wp = {"id": 99, "updatedAt": "2026-01-01"}
        eid = _stable_event_id(wp)
        assert eid.startswith("op_99_")
        assert len(eid.split("_")) == 3


class TestHashUser:
    def test_deterministic(self):
        assert _hash_user("user1", "salt") == _hash_user("user1", "salt")

    def test_different_salt(self):
        assert _hash_user("user1", "salt1") != _hash_user("user1", "salt2")

    def test_truncated(self):
        assert len(_hash_user("user1", "salt")) == 16


class TestExtractExternalKey:
    def test_from_direct_field(self):
        config = _make_config()
        wp = {"customField1": "REPO:issues/TASK-001.md"}
        assert _extract_external_key(config, wp) == "REPO:issues/TASK-001.md"

    def test_from_custom_fields_dict(self):
        config = _make_config()
        wp = {"customFields": {"customField1": "key-value"}}
        assert _extract_external_key(config, wp) == "key-value"

    def test_from_description_pattern(self):
        config = _make_config()
        wp = {"description": {"raw": "Some text\nsynctx_external_key: repo:path/file.md\nMore text"}}
        assert _extract_external_key(config, wp) == "repo:path/file.md"

    def test_no_key_returns_none(self):
        config = _make_config()
        wp = {"subject": "No key here"}
        assert _extract_external_key(config, wp) is None


class TestRedactWorkPackage:
    def test_filters_to_allowed_fields(self, monkeypatch):
        monkeypatch.setenv("OUTBOUND_FEEDBACK_HASH_SALT", "test-salt")
        config = _make_config()
        wp = {
            "id": 1,
            "customField1": "repo:task.md",
            "status": "open",
            "priority": "high",
            "description": {"raw": "sensitive data"},
            "author": {"id": 5},
            "updatedAt": "2026-01-01",
        }
        result = redact_work_package(config, wp)
        assert result is not None
        assert "status" in result["changes"]
        assert "priority" in result["changes"]
        assert "description" not in result["changes"]
        assert "description" in result["ignored_fields"]

    def test_returns_none_without_external_key(self, monkeypatch):
        monkeypatch.setenv("OUTBOUND_FEEDBACK_HASH_SALT", "test-salt")
        config = _make_config()
        wp = {"id": 1, "status": "open"}
        assert redact_work_package(config, wp) is None

    def test_changed_by_hash_present(self, monkeypatch):
        monkeypatch.setenv("OUTBOUND_FEEDBACK_HASH_SALT", "test-salt")
        config = _make_config()
        wp = {
            "id": 1,
            "customField1": "repo:task.md",
            "author": {"id": 42},
            "updatedAt": "2026-01-01",
        }
        result = redact_work_package(config, wp)
        assert "changed_by_hash" in result
        assert len(result["changed_by_hash"]) == 16

    def test_missing_salt_raises(self, monkeypatch):
        monkeypatch.delenv("OUTBOUND_FEEDBACK_HASH_SALT", raising=False)
        config = _make_config()
        wp = {"id": 1, "customField1": "repo:task.md", "updatedAt": "now"}
        with pytest.raises(ValueError, match="HASH_SALT"):
            redact_work_package(config, wp)

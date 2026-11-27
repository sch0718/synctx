"""External Mail Importer 단위 테스트."""

import json
import hmac
import hashlib
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("OUTBOUND_FEEDBACK_HMAC_KEY", "test-secret-key")

import external_importer as ei


@pytest.fixture(autouse=True)
def _reset_state(tmp_path, monkeypatch):
    monkeypatch.setattr(ei, "HMAC_KEY", b"test-secret-key")
    monkeypatch.setattr(ei, "REPLAY_WINDOW_MINUTES", 1440)
    monkeypatch.setattr(ei, "PROCESSED_EVENTS_FILE", tmp_path / "processed.json")


def _sign(payload: dict) -> str:
    return hmac.new(
        b"test-secret-key",
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(),
        hashlib.sha256,
    ).hexdigest()


class TestVerifyHmac:
    def test_valid_signature(self):
        payload = {"event_id": "e1", "changes": {}}
        sig = _sign(payload)
        assert ei.verify_hmac(payload, sig) is True

    def test_invalid_signature(self):
        assert ei.verify_hmac({"event_id": "e1"}, "bad") is False

    def test_accepts_exporter_signature(self):
        # Exporter가 보낸 메일 본문을 그대로 받아 검증한다(직렬화가 달라 늘 실패하던 회귀 방지)
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "services", "feedback", "src"))
        from signer import FeedbackSigner

        payload = {"event_id": "op_1_abc", "external_key": "docs/task.md", "changes": {"status": "closed"}}
        signed = FeedbackSigner(key="test-secret-key", key_id="active").sign(payload)
        body = json.loads(json.dumps({"payload": payload, **signed}, indent=2))
        assert ei.verify_hmac(body["payload"], body["signature"]) is True


class TestValidateRepoPath:
    def test_normal_path(self):
        assert ei.validate_repo_path("docs/task.md") == Path("docs/task.md")

    def test_traversal_rejected(self):
        with pytest.raises(ValueError, match="Invalid"):
            ei.validate_repo_path("../../etc/passwd")

    def test_absolute_normalized_safely(self):
        result = ei.validate_repo_path("/etc/passwd")
        assert str(result) == "etc/passwd"
        assert not str(result).startswith("/")


class TestApplyFeedbackChanges:
    def test_applies_allowed_fields(self, tmp_path):
        md = tmp_path / "task.md"
        md.write_text("---\nsynctx:\n  type: task\n  status: open\n---\n# Task\n")
        changed = ei.apply_feedback_changes(md, {"status": "closed", "priority": "high"})
        assert changed is True
        content = md.read_text()
        assert "closed" in content
        assert "high" in content

    def test_ignores_disallowed_fields(self, tmp_path):
        md = tmp_path / "task.md"
        md.write_text("---\nsynctx:\n  type: task\n---\n# Task\n")
        changed = ei.apply_feedback_changes(md, {"description": "hacked", "assignee": "evil"})
        assert changed is False

    def test_no_change_if_same_value(self, tmp_path):
        md = tmp_path / "task.md"
        md.write_text("---\nsynctx:\n  type: task\n  status: open\n---\n# Task\n")
        changed = ei.apply_feedback_changes(md, {"status": "open"})
        assert changed is False

    def test_dict_value_extracts_name(self, tmp_path):
        md = tmp_path / "task.md"
        md.write_text("---\nsynctx:\n  type: task\n---\n# Task\n")
        changed = ei.apply_feedback_changes(md, {"status": {"name": "in_progress", "id": 3}})
        assert changed is True
        assert "in_progress" in md.read_text()


class TestReplayProtection:
    def test_stores_processed_event(self, tmp_path):
        ei.save_processed_events({"e1": "2026-06-20T00:00:00"})
        events = ei.load_processed_events()
        assert "e1" in events

    def test_empty_initially(self, tmp_path):
        events = ei.load_processed_events()
        assert events == {}


class TestProcessEmailBody:
    def test_valid_email_creates_pr(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        task_file = tmp_path / "docs" / "task.md"
        task_file.parent.mkdir()
        task_file.write_text("---\nsynctx:\n  type: task\n  status: open\n---\n# Task\n")

        payload = {
            "event_id": "evt-001",
            "external_key": "default:docs/task.md",
            "changes": {"status": "closed"},
            "changed_by_hash": "abc123",
        }
        body = json.dumps({"payload": payload, "signature": _sign(payload)})

        calls = []
        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            result = MagicMock(returncode=0, stdout=b"main")
            result.stdout = result.stdout if isinstance(cmd, list) and "branch" in cmd else b""
            return result

        with patch("external_importer.subprocess.run", side_effect=mock_run):
            ei.process_email_body(body)

        git_cmds = [c[0] if isinstance(c, list) else c for c in calls]
        assert any("checkout" in str(c) for c in calls)
        assert any("commit" in str(c) for c in calls)
        assert any("push" in str(c) for c in calls)
        assert any("pr" in str(c) for c in calls)

    def test_invalid_hmac_rejected(self, monkeypatch, capsys):
        payload = {"event_id": "evt-002", "external_key": "x.md", "changes": {}}
        body = json.dumps({"payload": payload, "signature": "invalid"})

        ei.process_email_body(body)

    def test_replay_rejected(self, tmp_path, monkeypatch):
        ei.save_processed_events({"evt-dup": "2099-01-01T00:00:00"})

        payload = {"event_id": "evt-dup", "external_key": "x.md", "changes": {}}
        body = json.dumps({"payload": payload, "signature": _sign(payload)})

        calls = []
        with patch("external_importer.subprocess.run", side_effect=lambda *a, **k: calls.append(a)):
            ei.process_email_body(body)

        assert len(calls) == 0

    def test_invalid_json_rejected(self):
        ei.process_email_body("not json")


class TestConflictLabel:
    def test_pr_created_with_conflict_review_label(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        task_file = tmp_path / "task.md"
        task_file.write_text("---\nsynctx:\n  type: task\n  status: open\n---\n# T\n")

        payload = {
            "event_id": "evt-label",
            "external_key": "default:task.md",
            "changes": {"status": "done"},
            "changed_by_hash": "x",
        }
        body = json.dumps({"payload": payload, "signature": _sign(payload)})

        captured_cmds = []
        def mock_run(cmd, **kwargs):
            captured_cmds.append(cmd)
            return MagicMock(returncode=0, stdout=b"main")

        with patch("external_importer.subprocess.run", side_effect=mock_run):
            ei.process_email_body(body)

        pr_cmd = [c for c in captured_cmds if isinstance(c, list) and "pr" in c]
        assert len(pr_cmd) > 0
        pr_args = " ".join(pr_cmd[0])
        assert "conflict-review" in pr_args

import hashlib
import hmac
import json

import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from signer import FeedbackSigner


@pytest.fixture
def signer(monkeypatch):
    monkeypatch.setenv("OUTBOUND_FEEDBACK_HMAC_KEY", "test-key-123")
    monkeypatch.setenv("OUTBOUND_FEEDBACK_HMAC_KEY_ID", "k1")
    monkeypatch.delenv("OUTBOUND_FEEDBACK_PREVIOUS_HMAC_KEY", raising=False)
    return FeedbackSigner()


class TestFeedbackSigner:
    def test_missing_key_raises(self, monkeypatch):
        monkeypatch.delenv("OUTBOUND_FEEDBACK_HMAC_KEY", raising=False)
        with pytest.raises(ValueError, match="OUTBOUND_FEEDBACK_HMAC_KEY"):
            FeedbackSigner()

    def test_sign_returns_key_id_and_signature(self, signer):
        result = signer.sign({"a": 1})
        assert result["key_id"] == "k1"
        assert isinstance(result["signature"], str)
        assert len(result["signature"]) == 64

    def test_verify_own_signature(self, signer):
        payload = {"event_id": "op_1_abc", "status": "resolved"}
        sig = signer.sign(payload)
        assert signer.verify(payload, sig["signature"], sig["key_id"]) is True

    def test_verify_wrong_signature(self, signer):
        assert signer.verify({"a": 1}, "0" * 64, "k1") is False

    def test_verify_wrong_key_id(self, signer):
        sig = signer.sign({"a": 1})
        assert signer.verify({"a": 1}, sig["signature"], "wrong-key") is False

    def test_canonical_payload_sorted(self):
        assert FeedbackSigner.canonical_payload({"b": 2, "a": 1}) == '{"a":1,"b":2}'

    def test_key_rotation(self, monkeypatch):
        monkeypatch.setenv("OUTBOUND_FEEDBACK_HMAC_KEY", "new-key")
        monkeypatch.setenv("OUTBOUND_FEEDBACK_HMAC_KEY_ID", "k2")
        monkeypatch.setenv("OUTBOUND_FEEDBACK_PREVIOUS_HMAC_KEY", "old-key")
        monkeypatch.setenv("OUTBOUND_FEEDBACK_PREVIOUS_HMAC_KEY_ID", "k1")

        new_signer = FeedbackSigner()

        old_payload = {"x": 1}
        old_sig = hmac.new(b"old-key", FeedbackSigner.canonical_payload(old_payload).encode(), hashlib.sha256).hexdigest()

        assert new_signer.verify(old_payload, old_sig, "k1") is True

        new_sig = new_signer.sign(old_payload)
        assert new_sig["key_id"] == "k2"
        assert new_signer.verify(old_payload, new_sig["signature"], "k2") is True

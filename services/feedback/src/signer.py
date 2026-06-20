"""Outbound feedback HMAC signing helpers."""

from __future__ import annotations

import hashlib
import hmac
import json
import os


class FeedbackSigner:
    def __init__(self, key: str | None = None, key_id: str | None = None):
        key = key or os.getenv("OUTBOUND_FEEDBACK_HMAC_KEY")
        if not key:
            raise ValueError("OUTBOUND_FEEDBACK_HMAC_KEY must be set")
        self.key = key.encode("utf-8")
        self.key_id = key_id or os.getenv("OUTBOUND_FEEDBACK_HMAC_KEY_ID", "active")
        previous_key = os.getenv("OUTBOUND_FEEDBACK_PREVIOUS_HMAC_KEY")
        self.previous_key = previous_key.encode("utf-8") if previous_key else None
        self.previous_key_id = os.getenv("OUTBOUND_FEEDBACK_PREVIOUS_HMAC_KEY_ID", "previous")

    @staticmethod
    def canonical_payload(payload: dict) -> str:
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    def sign(self, payload: dict) -> dict:
        payload_str = self.canonical_payload(payload)
        signature = hmac.new(self.key, payload_str.encode("utf-8"), hashlib.sha256).hexdigest()
        return {"key_id": self.key_id, "signature": signature}

    def verify(self, payload: dict, signature: str, key_id: str | None = None) -> bool:
        payload_str = self.canonical_payload(payload)
        candidates = [(self.key_id, self.key)]
        if self.previous_key:
            candidates.append((self.previous_key_id, self.previous_key))
        for candidate_key_id, candidate_key in candidates:
            if key_id and key_id != candidate_key_id:
                continue
            expected = hmac.new(candidate_key, payload_str.encode("utf-8"), hashlib.sha256).hexdigest()
            if hmac.compare_digest(expected, signature):
                return True
        return False

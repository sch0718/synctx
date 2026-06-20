import os
import json
import time
from datetime import datetime, timezone
import logging
from typing import Dict, Any

from synctx_common.config import SynctxConfig
from event_store import EventStore
from mailer import send_feedback_email
from redactor import redact_work_package
from signer import FeedbackSigner

logger = logging.getLogger(__name__)

class FeedbackExporter:
    def __init__(self, config: SynctxConfig):
        self.config = config
        self.fb_config = config.outbound_feedback
        self.op_config = config.targets.openproject
        
        salt_env = os.getenv("OUTBOUND_FEEDBACK_HASH_SALT")
        hmac_env = os.getenv("OUTBOUND_FEEDBACK_HMAC_KEY")
        if not salt_env or not hmac_env:
            raise ValueError("OUTBOUND_FEEDBACK_HASH_SALT and OUTBOUND_FEEDBACK_HMAC_KEY must be set")
            
        self.signer = FeedbackSigner(hmac_env)
        self.event_store = EventStore()
        self.state_file = "/data/sync-state/feedback_state.json"

    def _get_last_timestamp(self) -> str:
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r") as f:
                    return json.load(f).get("last_timestamp")
            except Exception:
                pass
        # Default to 1 hour ago if no state
        return datetime.fromtimestamp(time.time() - 3600, tz=timezone.utc).isoformat()

    def _update_last_timestamp(self, ts: str):
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
        tmp_file = f"{self.state_file}.tmp"
        with open(tmp_file, "w") as f:
            json.dump({"last_timestamp": ts}, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_file, self.state_file)

    def _redact_and_format(self, wp: Dict[str, Any]) -> Dict[str, Any]:
        return redact_work_package(self.config, wp)

    async def run(self):
        if not self.fb_config.enabled:
            logger.info("Outbound feedback is disabled.")
            return

        last_ts = self._get_last_timestamp()
        current_ts = datetime.now(timezone.utc).isoformat()
        
        logger.info(f"Checking for updates since {last_ts}")
        
        updated_wps = []
        if self.op_config:
            import httpx
            token = os.getenv(self.op_config.auth.credential_env)
            async with httpx.AsyncClient(
                base_url=self.op_config.base_url.rstrip("/"),
                auth=("apikey", token) if token else None,
                timeout=30.0
            ) as client:
                # Fetch work packages updated since last_ts
                url = "/api/v3/work_packages"
                params = {
                    "filters": f'[{{"updatedAt":{{"operator":">=","values":["{last_ts}"]}}}}]'
                }
                try:
                    response = await client.get(url, params=params)
                    response.raise_for_status()
                    data = response.json()
                    updated_wps = data.get("_embedded", {}).get("elements", [])
                except Exception as e:
                    logger.error(f"Failed to fetch updated WPs from OpenProject: {e}")
                    return
        
        success_count = 0
        failure_count = 0
        skipped_count = 0
        for wp in updated_wps:
            payload = self._redact_and_format(wp)
            if not payload:
                skipped_count += 1
                continue
                
            if self.event_store.has_event(payload["event_id"]):
                skipped_count += 1
                continue
            signature = self.signer.sign(payload)
            final_message = {
                "payload": payload,
                **signature,
            }
            
            body = json.dumps(final_message, indent=2)
            subject = f"[synctx] Feedback Event {payload['event_id']}"
            
            try:
                send_feedback_email(self.fb_config.outbound_mail, subject, body)
                self.event_store.record(payload["event_id"], payload, final_message, "sent")
                success_count += 1
            except Exception as e:
                self.event_store.record(payload["event_id"], payload, final_message, "failed")
                failure_count += 1
                logger.error(f"Failed to process WP {wp['id']}: {e}")
                
        if failure_count == 0:
            self._update_last_timestamp(current_ts)
        else:
            logger.warning("Feedback export had failures; timestamp state was not advanced.")
        logger.info(
            "Feedback export completed. sent=%s skipped=%s failed=%s",
            success_count,
            skipped_count,
            failure_count,
        )

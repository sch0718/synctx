"""Outbound Feedback administrative API."""

from __future__ import annotations

import json

from fastapi import Depends, FastAPI, Header, HTTPException

from synctx_common.config import load_config
from synctx_common.observability import register_observability_routes

from event_store import EventStore
from mailer import send_feedback_email
from redactor import redact_work_package
from signer import FeedbackSigner


__version__ = "0.1.0"
app = FastAPI(title="synctx-feedback", version=__version__)
config = load_config()
register_observability_routes(app, version=__version__)


def verify_feedback_admin(x_forwarded_groups: str = Header(default="")):
    if not config.authz.enabled:
        return
    groups = [g.strip() for g in x_forwarded_groups.split(",") if g.strip()]
    allowed = config.authz.role_mappings.feedback_admin + config.authz.role_mappings.platform_admin
    if not any(group in allowed for group in groups):
        raise HTTPException(status_code=403, detail="Forbidden: feedback-admin role required")


@app.get("/api/feedback/events", dependencies=[Depends(verify_feedback_admin)])
def list_events(repo_id: str | None = None, since: str | None = None):
    return {"events": EventStore().list(repo_id=repo_id, since=since)}


@app.post("/api/feedback/dry-run", dependencies=[Depends(verify_feedback_admin)])
def dry_run(work_package: dict):
    payload = redact_work_package(config, work_package)
    if not payload:
        return {"exportable": False, "reason": "missing external key"}
    signature = FeedbackSigner().sign(payload)
    return {"exportable": True, "payload": payload, **signature}


@app.post("/api/feedback/resend/{event_id}", dependencies=[Depends(verify_feedback_admin)])
def resend(event_id: str):
    event = EventStore().get(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    message = event.get("message")
    if not message:
        raise HTTPException(status_code=409, detail="Event has no persisted message")
    send_feedback_email(
        config.outbound_feedback.outbound_mail,
        f"[synctx] Feedback Event {event_id}",
        json.dumps(message, indent=2, ensure_ascii=False),
    )
    return {"status": "resent", "event_id": event_id}

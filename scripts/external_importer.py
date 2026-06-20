#!/usr/bin/env python3
"""
External Mail Importer (Reference Implementation)
외부망에서 메일을 수신하여 HMAC을 검증하고 PR을 생성하는 스크립트.
"""

import json
import hmac
import hashlib
import os
import sys
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

import subprocess

HMAC_KEY_ENV = os.getenv("OUTBOUND_FEEDBACK_HMAC_KEY")
if not HMAC_KEY_ENV:
    raise RuntimeError("OUTBOUND_FEEDBACK_HMAC_KEY must be set")
HMAC_KEY = HMAC_KEY_ENV.encode()
REPLAY_WINDOW_MINUTES = int(os.getenv("OUTBOUND_FEEDBACK_REPLAY_WINDOW_MINUTES", "1440"))
PROCESSED_EVENTS_FILE = Path(os.getenv("OUTBOUND_FEEDBACK_PROCESSED_EVENTS_FILE", ".synctx-feedback/processed_events.json"))
ALLOWED_FIELDS = {"status", "priority", "version", "due_date"}
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

def load_processed_events():
    if PROCESSED_EVENTS_FILE.exists():
        with open(PROCESSED_EVENTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_processed_events(events):
    PROCESSED_EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = PROCESSED_EVENTS_FILE.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(events, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, PROCESSED_EVENTS_FILE)

def verify_hmac(payload: dict, signature: str) -> bool:
    payload_str = json.dumps(payload, sort_keys=True)
    expected = hmac.new(HMAC_KEY, payload_str.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)

def validate_repo_path(path: str) -> Path:
    normalized = os.path.normpath(path.strip("/"))
    if normalized.startswith("..") or normalized.startswith("/"):
        raise ValueError(f"Invalid repository path: {path}")
    return Path(normalized)

def load_markdown_with_frontmatter(path: Path):
    content = path.read_text(encoding="utf-8")
    match = FRONTMATTER_RE.match(content)
    if not match:
        return {}, content, ""
    data = yaml.safe_load(match.group(1)) or {}
    if not isinstance(data, dict):
        data = {}
    return data, content[match.end():], content[:match.end()]

def dump_markdown_with_frontmatter(frontmatter: dict, body: str) -> str:
    yaml_text = yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False).strip()
    return f"---\n{yaml_text}\n---\n{body}"

def apply_feedback_changes(file_path: Path, changes: dict) -> bool:
    safe_changes = {k: v for k, v in changes.items() if k in ALLOWED_FIELDS}
    if not safe_changes:
        logger.info("No allowed changes to apply for %s", file_path)
        return False

    frontmatter, body, _ = load_markdown_with_frontmatter(file_path)
    synctx = frontmatter.setdefault("synctx", {})
    if not isinstance(synctx, dict):
        synctx = {}
        frontmatter["synctx"] = synctx

    changed = False
    for key, value in safe_changes.items():
        normalized_value = value.get("name") if isinstance(value, dict) and "name" in value else value
        if synctx.get(key) != normalized_value:
            synctx[key] = normalized_value
            changed = True

    if not changed:
        return False

    file_path.write_text(dump_markdown_with_frontmatter(frontmatter, body), encoding="utf-8")
    return True

def process_email_body(body: str):
    try:
        data = json.loads(body)
        payload = data["payload"]
        signature = data["signature"]
    except Exception as e:
        logger.error(f"Invalid format: {e}")
        return

    # 1. Verify HMAC
    if not verify_hmac(payload, signature):
        logger.error("HMAC signature verification failed!")
        return

    # 2. Check Replay
    event_id = payload["event_id"]
    events = load_processed_events()
    
    # Cleanup old events
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=REPLAY_WINDOW_MINUTES)
    def _parse_ts(v):
        dt = datetime.fromisoformat(v)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    events = {k: v for k, v in events.items() if _parse_ts(v) > cutoff}
    
    if event_id in events:
        logger.warning(f"Replay detected for event {event_id}. Skipping.")
        return
        
    events[event_id] = datetime.now(timezone.utc).isoformat()
    save_processed_events(events)

    # 3. Create PR
    external_key = payload["external_key"]
    changes = payload["changes"]
    
    logger.info(f"Valid feedback received for {external_key}")
    logger.info(f"Changes to apply: {changes}")
    
    if ":" in external_key:
        repo_id, file_path = external_key.split(":", 1)
    else:
        repo_id, file_path = "default", external_key
        
    branch_name = f"synctx-feedback/{re.sub(r'[^A-Za-z0-9._-]', '-', event_id)}"
    current_branch = ""
    
    try:
        current_branch = subprocess.run(
            ["git", "branch", "--show-current"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        # 1. Checkout new branch
        subprocess.run(["git", "checkout", "-b", branch_name], check=True, capture_output=True)
        
        target_path = validate_repo_path(file_path)
        if target_path.exists():
            changed = apply_feedback_changes(target_path, changes)
            if not changed:
                logger.info("No file changes produced for %s", target_path)
                return

            # 3. Commit
            subprocess.run(["git", "add", str(target_path)], check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", f"chore(feedback): update from {payload.get('changed_by_hash', 'unknown')} ({event_id})"], check=True, capture_output=True)
            
            # 4. Create PR
            # Requires gh cli
            subprocess.run(["git", "push", "-u", "origin", branch_name], check=True, capture_output=True)
            subprocess.run(["gh", "pr", "create", "--title", f"Feedback update for {target_path}", "--body", f"Applied changes: {json.dumps(changes, ensure_ascii=False)}", "--label", "conflict-review"], check=True, capture_output=True)
            logger.info(f"Successfully created PR for {event_id}")
        else:
            logger.error(f"File {target_path} not found.")
            
    except subprocess.CalledProcessError as e:
        logger.error(f"Git/GH command failed: {e.stderr.decode() if e.stderr else e}")
    except ValueError as e:
        logger.error(str(e))
    finally:
        if current_branch:
            subprocess.run(["git", "checkout", current_branch], check=False, capture_output=True)

if __name__ == "__main__":
    # Example usage: cat email_body.json | python external_importer.py
    body = sys.stdin.read()
    process_email_body(body)

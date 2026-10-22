import json
import os
import sys

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from exporter import FeedbackExporter
from synctx_common.config import (
    OpenProjectTargetConfig, OutboundFeedbackConfig, SynctxConfig, TargetAuthConfig, TargetsConfig,
)

REAL_CLIENT = httpx.AsyncClient


@pytest.mark.asyncio
async def test_updated_since_filter_uses_operator_openproject_accepts(tmp_path, monkeypatch):
    # OpenProject는 updatedAt에 >=를 받지 않고 400을 낸다. 날짜·시각 필터는 <>d(시작만 지정)로 거른다
    monkeypatch.setenv("OUTBOUND_FEEDBACK_HASH_SALT", "salt")
    monkeypatch.setenv("OUTBOUND_FEEDBACK_HMAC_KEY", "key")
    monkeypatch.setenv("OUTBOUND_FEEDBACK_EVENT_STORE", str(tmp_path / "feedback_events.json"))
    seen = []

    def handler(request):
        seen.append(json.loads(request.url.params["filters"]))
        return httpx.Response(200, json={"_embedded": {"elements": []}})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(handler), **kw))
    config = SynctxConfig(
        targets=TargetsConfig(
            openproject=OpenProjectTargetConfig(
                base_url="https://op.internal",
                auth=TargetAuthConfig(type="token", credential_env="OP_TOKEN"),
                external_key_custom_field="customField1",
            )
        ),
        outbound_feedback=OutboundFeedbackConfig(enabled=True, allowed_fields=["status"]),
    )
    exporter = FeedbackExporter(config)
    exporter.state_file = str(tmp_path / "feedback_state.json")
    (tmp_path / "feedback_state.json").write_text(json.dumps({"last_timestamp": "2026-10-22T11:57:22.904047+00:00"}))

    await exporter.run()

    assert seen == [[{"updatedAt": {"operator": "<>d", "values": ["2026-10-22T11:57:22.904047+00:00", ""]}}]]


@pytest.mark.asyncio
async def test_all_pages_of_updated_work_packages_are_read(tmp_path, monkeypatch):
    # OpenProject는 한 페이지(기본 20건)만 돌려준다. 첫 페이지만 보고 마지막 처리 시각을 옮기면 나머지는 다시 보지 않는다
    monkeypatch.setenv("OUTBOUND_FEEDBACK_HASH_SALT", "salt")
    monkeypatch.setenv("OUTBOUND_FEEDBACK_HMAC_KEY", "key")
    monkeypatch.setenv("OUTBOUND_FEEDBACK_EVENT_STORE", str(tmp_path / "feedback_events.json"))
    work_packages = [
        {"id": i, "updatedAt": "2026-10-22T00:00:00Z", "customField1": f"r:{i}.md", "status": "done"} for i in range(1, 46)
    ]
    pages = []

    def handler(request):
        offset = int(request.url.params["offset"])
        size = int(request.url.params["pageSize"])
        pages.append(offset)
        chunk = work_packages[(offset - 1) * size:offset * size]
        return httpx.Response(200, json={"total": len(work_packages), "count": len(chunk), "_embedded": {"elements": chunk}})

    sent = []
    import exporter as exporter_module
    monkeypatch.setattr(exporter_module, "send_feedback_email", lambda config, subject, body: sent.append(json.loads(body)["payload"]["work_package_id"]))
    monkeypatch.setattr(exporter_module.FeedbackExporter, "PAGE_SIZE", 20)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(handler), **kw))
    config = SynctxConfig(
        targets=TargetsConfig(
            openproject=OpenProjectTargetConfig(
                base_url="https://op.internal",
                auth=TargetAuthConfig(type="token", credential_env="OP_TOKEN"),
                external_key_custom_field="customField1",
            )
        ),
        outbound_feedback=OutboundFeedbackConfig(enabled=True, allowed_fields=["status"]),
    )
    exporter = FeedbackExporter(config)
    exporter.state_file = str(tmp_path / "feedback_state.json")

    await exporter.run()

    assert pages == [1, 2, 3]
    assert sorted(sent) == list(range(1, 46))

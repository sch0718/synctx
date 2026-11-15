import asyncio
import hashlib
import logging
import os
import time
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

import commands
from adapters.mattermost import (MAX_FORM_BYTES, MattermostAdapter, inbound_from_form, parse_command_form,
                                 verify_command_token)
from ratelimit import RateLimiter
from runner import Runner
from gateway_state import StateStore
from synctx_common.config import load_config
from synctx_common.logging import configure_logging
from synctx_common.observability import collab_commands_total, register_observability_routes
from viewer_client import COMMAND_TIMEOUT_SECONDS, ViewerClient, ViewerError

__version__ = "0.8.1"
configure_logging(service_name="collab-gateway")
logger = logging.getLogger(__name__)

config = load_config()
STATE_PATH_ENV = "SYNCTX_COLLAB_STATE_PATH"
COMMAND_PATH = "/api/collab/mattermost/command"
CACHE_AGE_FACTOR = 3

for _command in commands.COMMANDS:
    for _result in commands.RESULTS:
        collab_commands_total.labels(command=_command, result=_result)


class GatewayRunner(Runner):
    """폴링 주기 실행기에 종료 때 닫을 HTTP 클라이언트를 묶는다."""

    async def aclose(self) -> None:
        await self._adapter.aclose()
        await self._viewer.aclose()


def _required_env(name: str) -> str:
    value = os.getenv(name, "")
    if not value:
        raise RuntimeError(f"환경변수 {name}가 비어 있습니다(collab.gateway 토큰은 Secret으로 주입해야 합니다)")
    return value


def create_runner() -> GatewayRunner:
    gateway = config.collab.gateway
    bot_token = _required_env(gateway.mattermost.bot_token_env)
    service_token = _required_env(gateway.viewer_service_token_env)
    store = StateStore(os.getenv(STATE_PATH_ENV) or gateway.state_path)
    return GatewayRunner(gateway, config.runtime.public_base_url, ViewerClient(gateway.viewer_url, service_token),
                         MattermostAdapter(gateway.mattermost.url, bot_token), store)


def _poll_interval() -> float:
    return float(config.collab.gateway.poll_interval_seconds)


async def _poll_loop(runner) -> None:
    while True:
        try:
            await runner.run_cycle()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("collab_cycle_error error=%s", type(exc).__name__)
        await asyncio.sleep(_poll_interval())


@asynccontextmanager
async def lifespan(application: FastAPI):
    application.state.runner = None
    application.state.limiter = None
    if not config.collab.gateway.enabled:
        logger.warning("collab.gateway.enabled=false: 알림을 보내지 않고 대기합니다")
        yield
        return
    runner = create_runner()
    application.state.runner = runner
    application.state.limiter = RateLimiter(config.collab.gateway.command_rate_limit_per_minute)
    task = asyncio.create_task(_poll_loop(runner))
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        await runner.aclose()


app = FastAPI(title="synctx-collab-gateway", description="Collab Gateway for synctx", version=__version__,
              lifespan=lifespan)
register_observability_routes(app, version=__version__, service_name="collab-gateway")


def _channel_keys(team: str, channel: str) -> set[str]:
    return {key for key, target in config.collab.gateway.channels.items()
            if target.team.lower() == team and target.channel.lower() == channel}


def _reply(text: str) -> JSONResponse:
    return JSONResponse({"response_type": "ephemeral", "text": text})


async def _read_body(request: Request) -> bytes | None:
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_FORM_BYTES:
            return None
    return body


@app.post(COMMAND_PATH)
async def mattermost_command(request: Request):
    gateway = config.collab.gateway
    runner = request.app.state.runner
    expected = os.getenv(gateway.mattermost.command_token_env, "")
    if runner is None or not gateway.commands_enabled or not expected:
        return PlainTextResponse("commands disabled", status_code=503)
    body = await _read_body(request)
    if body is None:
        return PlainTextResponse("request too large", status_code=413)
    try:
        form = parse_command_form(body)
    except ValueError:
        return PlainTextResponse("bad request", status_code=400)
    if not verify_command_token(form.get("token", ""), expected):
        collab_commands_total.labels(command="unknown", result="unauthorized").inc()
        return PlainTextResponse("unauthorized", status_code=401)
    inbound = inbound_from_form(form)
    if inbound is None:
        return PlainTextResponse("bad request", status_code=400)

    started = time.monotonic()
    command, _ = commands.parse(inbound.text)
    keys = _channel_keys(inbound.team, inbound.channel)
    result = "error"
    try:
        if not request.app.state.limiter.allow(inbound.user_id):
            result, text = "rate_limited", "요청이 너무 많습니다. 잠시 후 다시 시도하세요."
        else:
            now = datetime.now(timezone.utc)
            meetings = []
            async with asyncio.timeout(COMMAND_TIMEOUT_SECONDS):
                initial = commands.handle(inbound.text, meetings, keys, now, config.runtime.public_base_url)
                needs_meetings = command in ("today", "upcoming", "meeting", "join", "summary", "search", "evidence")
                if needs_meetings and keys and initial.result != "invalid":
                    meetings = await runner.current_meetings(now, timedelta(seconds=gateway.poll_interval_seconds * CACHE_AGE_FACTOR))
                if meetings is None:
                    raise ViewerError("Viewer 회의 목록을 읽지 못했습니다")
                reply = commands.handle(inbound.text, meetings, keys, now, config.runtime.public_base_url)
                if reply.lookup is not None:
                    if command == "search":
                        repo_ids = {m.repo_id for m in meetings if m.channel in keys}
                        data = {"search_results": await runner.search_documents(reply.lookup.query, repo_ids)}
                    else:
                        meeting = reply.lookup.meeting
                        data = {"evidence_results": await runner.fetch_evidence(meeting.repo_id, meeting.id)}
                    reply = commands.handle(inbound.text, meetings, keys, now, config.runtime.public_base_url, **data)
                command, result, text = reply.command, reply.result, reply.text
    except (ViewerError, asyncio.TimeoutError):
        result, text = "unavailable", commands.unavailable_text(command)
    except Exception as exc:
        logger.error("collab_command_error command=%s error=%s", command, type(exc).__name__)
        text = "명령을 처리하지 못했습니다. 잠시 후 다시 시도하세요."
    collab_commands_total.labels(command=command, result=result).inc()
    logger.info("collab_command command=%s result=%s channel=%s user=%s ms=%d", command, result,
                ",".join(sorted(keys)) or "-", hashlib.sha256(inbound.user_id.encode()).hexdigest()[:12],
                (time.monotonic() - started) * 1000)
    return _reply(text)

import json
import logging
from pathlib import Path
import subprocess
import sys

import pytest

from synctx_common.logging import configure_logging, SynctxJsonFormatter


@pytest.mark.parametrize("module,service", [("feedback_cli", "feedback"), ("synctx_common.cli", "synctx")])
def test_cli_logs_are_structured_and_mask_secrets(monkeypatch, module, service):
    root = Path(__file__).resolve().parents[3]
    monkeypatch.setenv("PYTHONPATH", str(root / "services" / "feedback" / "src"))
    monkeypatch.setenv("SYNCTX_LOG_FORMAT", "json")
    monkeypatch.setenv("SYNCTX_LOG_LEVEL", "INFO")
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}; import logging; logging.info('token=example')"],
        capture_output=True, text=True, check=True,
    )
    record = json.loads(result.stderr)
    assert record["service"] == service
    assert record["message"] == "token=***"


class TestConfigureLogging:
    def test_json_format_default(self, capfd):
        configure_logging(service_name="cdn")
        logger = logging.getLogger("test.json")
        logger.info("upload complete")

        captured = capfd.readouterr()
        record = json.loads(captured.err.strip())
        assert record["service"] == "cdn"
        assert record["message"] == "upload complete"
        assert "timestamp" in record
        assert record["level"] == "INFO"

    def test_text_format(self, capfd, monkeypatch):
        monkeypatch.setenv("SYNCTX_LOG_FORMAT", "text")
        configure_logging(service_name="viewer")
        logger = logging.getLogger("test.text")
        logger.info("render done")

        captured = capfd.readouterr()
        assert "[viewer]" in captured.err
        assert "render done" in captured.err

    def test_secret_masking(self, capfd):
        configure_logging(service_name="sync")
        logger = logging.getLogger("test.mask")
        logger.info("connecting token=ghp_abc123secret")

        captured = capfd.readouterr()
        record = json.loads(captured.err.strip())
        assert "ghp_abc123secret" not in record["message"]
        assert "token=***" in record["message"]

    def test_log_level_from_env(self, monkeypatch):
        monkeypatch.setenv("SYNCTX_LOG_LEVEL", "DEBUG")
        monkeypatch.setenv("SYNCTX_LOG_FORMAT", "json")
        configure_logging(service_name="test")
        assert logging.getLogger().level == logging.DEBUG


@pytest.mark.parametrize("log_format", ["text", "json"])
@pytest.mark.parametrize("prefix", ["", "/view"])
@pytest.mark.parametrize("logger_name", ["viewer.render", "uvicorn.access"])
def test_render_token_masking_in_messages_args_and_exceptions(capfd, monkeypatch, log_format, prefix, logger_name):
    monkeypatch.setenv("SYNCTX_LOG_FORMAT", log_format)
    access = logging.getLogger("uvicorn.access")
    monkeypatch.setattr(access, "handlers", [])
    monkeypatch.setattr(access, "propagate", True)
    configure_logging(service_name="viewer")
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)
    token = "v1.1234567890.bWFpbg." + "a" * 40 + ".abcdefghijklmnopqrstuv"
    url = f"{prefix}/render/{token}/test-repo/docs/index.html"
    logger.info(f"GET {url} HTTP/1.1 200")
    logger.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1", "GET", url, "1.1", 403)
    try:
        raise RuntimeError(f"failed https://viewer.local{url}")
    except RuntimeError:
        logger.exception("request failed %s", url)
    output = capfd.readouterr().err
    assert token not in output
    assert f"{prefix}/render/***/test-repo/docs/index.html" in output
    assert "200" in output and "403" in output
    assert "RuntimeError" in output
    if log_format == "json":
        records = [json.loads(line) for line in output.splitlines()]
        assert len(records) == 3
        assert "exc_info" in records[-1]


def test_json_formatter_masks_nested_context_and_exception():
    formatter = SynctxJsonFormatter(service_name="viewer")
    record = logging.LogRecord("viewer", logging.ERROR, __file__, 1, "failed %s", ("/render/private/repo/a.htm",), None)
    record.context = {"url": "/view/render/other-private/repo/a.htm"}
    output = formatter.format(record)
    assert "private" not in output
    assert json.loads(output)["context"]["url"] == "/view/render/***/repo/a.htm"


@pytest.mark.parametrize("log_format", ["text", "json"])
def test_uvicorn_own_access_handler_masks_argument_urls(monkeypatch, log_format):
    import io
    from uvicorn.logging import AccessFormatter

    from synctx_common.logging import RenderTokenFormatter

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(AccessFormatter(fmt='%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False))
    access = logging.getLogger("uvicorn.access")
    monkeypatch.setattr(access, "handlers", [handler])
    monkeypatch.setattr(access, "propagate", False)
    monkeypatch.setenv("SYNCTX_LOG_FORMAT", log_format)
    configure_logging("viewer")
    configure_logging("viewer")
    assert isinstance(handler.formatter, RenderTokenFormatter)
    assert isinstance(handler.formatter.formatter, AccessFormatter)
    access.warning('%s - "%s %s HTTP/%s" %d', "127.0.0.1:80", "GET", "/view/render/access-secret/repo/a.html", "1.1", 200)
    output = stream.getvalue()
    assert "access-secret" not in output
    assert '/view/render/***/repo/a.html' in output
    assert "200 OK" in output


def test_open_route_token_is_masked_like_render():
    formatter = SynctxJsonFormatter(service_name="viewer")
    record = logging.LogRecord("viewer", logging.INFO, __file__, 1, "GET %s", ("/view/open/secret-token/repo/a.html",), None)
    output = formatter.format(record)
    assert "secret-token" not in output and "/view/open/***/repo/a.html" in output

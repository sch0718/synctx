import json
import logging

from synctx_common.logging import configure_logging, SynctxJsonFormatter


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

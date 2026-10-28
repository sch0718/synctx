import pytest
from pydantic import ValidationError

from synctx_common.config import CollabGatewayConfig, SynctxConfig, load_config


def gateway(**overrides):
    base = {
        "enabled": True,
        "mattermost": {"url": "https://chat.synctx.local"},
        "channels": {"team-docs": {"team": "devteam", "channel": "team-docs"}},
    }
    base.update(overrides)
    return base


def test_default_disabled_with_safe_defaults():
    cfg = SynctxConfig()
    gw = cfg.collab.gateway
    assert gw.enabled is False and gw.adapter == "mattermost"
    assert gw.reminders == [1440, 10] and gw.post_summary is False
    assert gw.poll_interval_seconds == 60
    assert gw.state_path == "/data/collab-gateway"
    assert gw.commands_enabled is True and gw.command_rate_limit_per_minute == 10
    assert gw.viewer_url == "http://synctx-viewer"
    assert gw.viewer_service_token_env == "VIEWER_SERVICE_TOKEN"
    assert gw.mattermost.bot_token_env == "MATTERMOST_BOT_TOKEN"
    assert gw.mattermost.command_token_env == "MATTERMOST_COMMAND_TOKEN"


def test_valid_full_block():
    gw = SynctxConfig.model_validate({"collab": {"gateway": gateway(reminders=[60, 1440, 10], post_summary=True)}}).collab.gateway
    assert gw.reminders == [1440, 60, 10]
    assert gw.channels["team-docs"].team == "devteam"
    assert gw.mattermost.url == "https://chat.synctx.local"


def test_trailing_slash_stripped_from_urls():
    gw = CollabGatewayConfig.model_validate(gateway(viewer_url="http://v:80/", mattermost={"url": "http://chat/"}))
    assert gw.viewer_url == "http://v:80" and gw.mattermost.url == "http://chat"


@pytest.mark.parametrize("url", ["", "chat.synctx.local", "ftp://chat", "https://user:pw@chat", "https://chat/path?x=1", "https://"])
def test_bad_mattermost_url_rejected_when_enabled(url):
    with pytest.raises(ValidationError):
        CollabGatewayConfig.model_validate(gateway(mattermost={"url": url}))


def test_enabled_requires_channels():
    with pytest.raises(ValidationError):
        CollabGatewayConfig.model_validate(gateway(channels={}))


def test_disabled_allows_incomplete_block():
    CollabGatewayConfig.model_validate({"enabled": False})


@pytest.mark.parametrize("reminders", [[0], [-1], [10081], [10, 10], ["a"], [True]])
def test_bad_reminders_rejected(reminders):
    with pytest.raises(ValidationError):
        CollabGatewayConfig.model_validate(gateway(reminders=reminders))


def test_empty_reminders_allowed():
    assert CollabGatewayConfig.model_validate(gateway(reminders=[])).reminders == []


@pytest.mark.parametrize("key", ["DX", "한글", "-x", "a b", "x" * 65])
def test_channel_key_must_match_meeting_channel_format(key):
    with pytest.raises(ValidationError):
        CollabGatewayConfig.model_validate(gateway(channels={key: {"team": "t", "channel": "c"}}))


@pytest.mark.parametrize("field", ["team", "channel"])
def test_channel_mapping_requires_nonempty_names(field):
    mapping = {"team": "t", "channel": "c", field: " "}
    with pytest.raises(ValidationError):
        CollabGatewayConfig.model_validate(gateway(channels={"x": mapping}))


@pytest.mark.parametrize("name", ["bot_token_env", "command_token_env"])
def test_env_names_validated(name):
    with pytest.raises(ValidationError):
        CollabGatewayConfig.model_validate(gateway(mattermost={"url": "https://chat", name: "bad name"}))
    with pytest.raises(ValidationError):
        CollabGatewayConfig.model_validate(gateway(viewer_service_token_env="lower"))


def test_poll_interval_lower_bound():
    with pytest.raises(ValidationError):
        CollabGatewayConfig.model_validate(gateway(poll_interval_seconds=5))


def test_unsupported_adapter_rejected():
    with pytest.raises(ValidationError):
        CollabGatewayConfig.model_validate(gateway(adapter="slack"))


def test_loaded_from_yaml(tmp_path):
    path = tmp_path / ".synctx.yaml"
    path.write_text(
        "collab:\n  gateway:\n    enabled: true\n    mattermost:\n      url: https://chat.synctx.local\n"
        "    channels:\n      dx:\n        team: devteam\n        channel: dx\n",
        encoding="utf-8",
    )
    assert load_config(path).collab.gateway.channels["dx"].channel == "dx"


def test_repo_level_collab_unchanged():
    cfg = SynctxConfig.model_validate({"repositories": [
        {"id": "r", "name": "R", "url": "https://git.test/r.git", "collab": {"enabled": True}}]})
    assert cfg.repositories[0].collab.enabled is True


@pytest.mark.parametrize("value", [0, -1, 601])
def test_command_rate_limit_bounds(value):
    with pytest.raises(ValidationError):
        CollabGatewayConfig.model_validate(gateway(command_rate_limit_per_minute=value))


def test_commands_can_be_disabled():
    assert CollabGatewayConfig.model_validate(gateway(commands_enabled=False)).commands_enabled is False

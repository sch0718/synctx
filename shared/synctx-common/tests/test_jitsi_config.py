import pytest
from pydantic import ValidationError

from synctx_common.config import JitsiConfig, SynctxConfig


def test_defaults_and_disabled_settings_are_not_validated():
    assert not SynctxConfig().collab.jitsi.enabled
    cfg = JitsiConfig(public_url="bad", app_id="", secret_env="lower", room_secret_env="?")
    assert not cfg.enabled


def test_enabled_config_normalizes_origin():
    cfg = JitsiConfig(enabled=True, public_url=" https://meet.synctx.local:8443/ ", app_id="synctx")
    assert cfg.public_url == "https://meet.synctx.local:8443"
    assert cfg.secret_env == "JITSI_APP_SECRET"
    assert cfg.room_secret_env == "JITSI_ROOM_SECRET"
    assert cfg.xmpp_domain == "meet.synctx.local"
    assert cfg.presenter_header == "X-Forwarded-User"


def test_xmpp_domain_is_independent_of_public_origin():
    cfg = JitsiConfig(enabled=True, public_url="https://meet.synctx.local:8443", app_id="synctx",
                      xmpp_domain=" XMPP.SYNCTX.LOCAL ")
    assert cfg.xmpp_domain == "xmpp.synctx.local"


@pytest.mark.parametrize("domain", ["https://meet.synctx.local", "meet.synctx.local:8443", "a/b", "a..b", "-a.b",
                                    "a_.b", "a.b-", "a\nb", "a" * 64 + ".b", ".".join(["a" * 63] * 4)])
def test_rejects_invalid_xmpp_domain(domain):
    with pytest.raises(ValidationError):
        JitsiConfig(enabled=True, public_url="https://meet.synctx.local", app_id="synctx", xmpp_domain=domain)


@pytest.mark.parametrize("header", ["X-Forwarded-User", "X-Forwarded-Email", "X-Forwarded-Preferred-Username"])
def test_presenter_header_allowlist(header):
    assert JitsiConfig(presenter_header=header).presenter_header == header


@pytest.mark.parametrize("header", ["Authorization", "X-User", "", "X-Forwarded-User\n"])
def test_rejects_untrusted_presenter_header(header):
    with pytest.raises(ValidationError):
        JitsiConfig(presenter_header=header)


@pytest.mark.parametrize("url", ["", "http://meet.synctx.local", "https://user@meet.synctx.local", "https://meet.synctx.local/path",
                                  "https://meet.synctx.local?q=1", "https://meet.synctx.local#x", "https://meet.synctx.local:65536",
                                  "https://meet.synctx.local:0", "https://meet.synctx.local\n.evil/path"])
def test_rejects_invalid_origins(url):
    with pytest.raises(ValidationError):
        JitsiConfig(enabled=True, public_url=url, app_id="synctx")


@pytest.mark.parametrize("field,value", [("app_id", ""), ("app_id", "a\nb"), ("app_id", "a" * 129),
                                         ("secret_env", "lower"), ("room_secret_env", "A-B"), ("secret_env", "")])
def test_rejects_invalid_names(field, value):
    args = dict(enabled=True, public_url="https://meet.synctx.local", app_id="synctx")
    args[field] = value
    with pytest.raises(ValidationError):
        JitsiConfig(**args)


def test_room_and_app_use_separate_slots():
    with pytest.raises(ValidationError):
        JitsiConfig(enabled=True, public_url="https://meet.synctx.local", app_id="synctx",
                    secret_env="SAME", room_secret_env="SAME")

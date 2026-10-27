import pytest
from pydantic import ValidationError

from synctx_common.config import (
    HtmlEmbedConfig,
    PlatformHtmlEmbedConfig,
    RepoProfile,
    SynctxConfig,
    ViewerConfig,
    normalize_origin,
)


def _repo(**html_embed):
    return {"id": "r", "name": "R", "url": "https://git.test/r.git", "html_embed": html_embed}


def test_defaults():
    repo = RepoProfile(id="r", name="R", url="https://git.test/r.git")
    assert repo.html_embed.enabled is False
    assert repo.html_embed.render_files is False
    assert repo.html_embed.allowed_origins == []
    assert repo.html_embed.max_bundle_bytes == 2097152
    assert SynctxConfig().html_embed.allowed_origins == []
    assert SynctxConfig().viewer.render_token_ttl_seconds == 14400


def test_origins_are_normalized_and_deduplicated():
    cfg = PlatformHtmlEmbedConfig(
        allowed_origins=["HTTPS://CDN.Synctx.Local:443", "https://cdn.synctx.local:8443", "https://cdn.synctx.local"]
    )
    assert cfg.allowed_origins == ["https://cdn.synctx.local", "https://cdn.synctx.local:8443"]


@pytest.mark.parametrize(
    "value",
    [
        "cdn.synctx.local",
        "http://cdn.synctx.local",
        "https://cdn.synctx.local/",
        "https://cdn.synctx.local/lib",
        "https://*.synctx.local",
        "https://cdn.synctx.local:0",
        "https://cdn.synctx.local:70000",
        "https://cdn.synctx.local?x=1",
        "",
    ],
)
def test_invalid_origins_are_rejected(value):
    with pytest.raises(ValueError):
        normalize_origin(value)
    with pytest.raises(ValidationError):
        HtmlEmbedConfig(allowed_origins=[value])


def test_repo_origins_must_be_subset_of_platform():
    with pytest.raises(ValidationError) as exc:
        SynctxConfig.model_validate(
            {
                "html_embed": {"allowed_origins": ["https://cdn.synctx.local"]},
                "repositories": [_repo(enabled=True, allowed_origins=["https://other.synctx.local"])],
            }
        )
    assert "https://other.synctx.local" in str(exc.value)


def test_subset_check_uses_normalized_values():
    cfg = SynctxConfig.model_validate(
        {
            "html_embed": {"allowed_origins": ["https://cdn.synctx.local"]},
            "repositories": [_repo(enabled=True, allowed_origins=["https://CDN.synctx.local:443"])],
        }
    )
    assert cfg.repositories[0].html_embed.allowed_origins == ["https://cdn.synctx.local"]


def test_max_bundle_bytes_must_be_positive():
    with pytest.raises(ValidationError):
        HtmlEmbedConfig(max_bundle_bytes=0)


def _xwiki_target(cred_env="XWIKI_CRED"):
    return {"xwiki": {"base_url": "https://xwiki.internal", "auth": {"type": "basic", "credential_env": cred_env}}}


def test_enabled_repo_needs_dedicated_xwiki_account_when_xwiki_is_a_target():
    with pytest.raises(ValidationError) as exc:
        SynctxConfig.model_validate({"targets": _xwiki_target(), "repositories": [_repo(enabled=True)]})
    assert "xwiki_credential_env" in str(exc.value)


def test_dedicated_account_must_differ_from_default_sync_account():
    with pytest.raises(ValidationError):
        SynctxConfig.model_validate(
            {"targets": _xwiki_target(), "repositories": [_repo(enabled=True, xwiki_credential_env="XWIKI_CRED")]}
        )


def test_dedicated_account_is_optional_without_xwiki_or_when_disabled():
    SynctxConfig.model_validate({"repositories": [_repo(enabled=True)]})
    SynctxConfig.model_validate({"targets": _xwiki_target(), "repositories": [_repo(enabled=False)]})
    cfg = SynctxConfig.model_validate(
        {"targets": _xwiki_target(), "repositories": [_repo(enabled=True, xwiki_credential_env="XWIKI_HTML_CRED")]}
    )
    assert cfg.repositories[0].html_embed.xwiki_credential_env == "XWIKI_HTML_CRED"


@pytest.mark.parametrize("ttl", [1, 60, 14400, 86400])
def test_render_token_ttl_accepts_positive_values(ttl):
    assert ViewerConfig(render_token_ttl_seconds=ttl).render_token_ttl_seconds == ttl
    cfg = SynctxConfig.model_validate({"viewer": {"render_token_ttl_seconds": ttl}})
    assert cfg.viewer.render_token_ttl_seconds == ttl


@pytest.mark.parametrize("ttl", [0, -1, -14400])
def test_render_token_ttl_rejects_zero_and_negative_values(ttl):
    with pytest.raises(ValidationError):
        ViewerConfig(render_token_ttl_seconds=ttl)


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("render_files", [False, True])
def test_html_embed_and_render_files_are_independent_opt_ins(enabled, render_files):
    cfg = SynctxConfig.model_validate({"repositories": [_repo(enabled=enabled, render_files=render_files)]})
    assert cfg.repositories[0].html_embed.enabled is enabled
    assert cfg.repositories[0].html_embed.render_files is render_files

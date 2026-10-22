from synctx_common.config import MarpConfig, RepoAuthConfig, SynctxConfig


def test_publish_auth_default_unset():
    assert MarpConfig().publish_auth is None


def test_publish_auth_from_dict():
    cfg = SynctxConfig.model_validate({
        "marp": {
            "publish_repo": "https://git.test/slides.git",
            "publish_auth": {"type": "token", "credential_env": "SLIDES_PUSH_TOKEN"},
        }
    })
    assert cfg.marp.publish_auth == RepoAuthConfig(type="token", credential_env="SLIDES_PUSH_TOKEN")

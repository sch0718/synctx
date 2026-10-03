from synctx_common.config import CollabRepoConfig, RepoProfile, SynctxConfig


def test_collab_default_disabled():
    repo = RepoProfile(id="r", name="R", url="https://git.test/r.git")
    assert isinstance(repo.collab, CollabRepoConfig)
    assert repo.collab.enabled is False


def test_collab_enabled_from_dict():
    cfg = SynctxConfig.model_validate({
        "repositories": [
            {"id": "r", "name": "R", "url": "https://git.test/r.git", "collab": {"enabled": True}},
            {"id": "s", "name": "S", "url": "https://git.test/s.git"},
        ]
    })
    assert cfg.repositories[0].collab.enabled is True
    assert cfg.repositories[1].collab.enabled is False

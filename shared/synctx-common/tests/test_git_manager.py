import pytest
from synctx_common.git_manager import GitRepoManager
from synctx_common.config import RepoProfile

def test_validate_path():
    profile = RepoProfile(id="test", name="test", url="dummy")
    manager = GitRepoManager(profile, "/tmp")
    
    assert manager.validate_path("docs/test.md") == "docs/test.md"
    assert manager.validate_path("/docs/test.md") == "docs/test.md"
    
    with pytest.raises(ValueError):
        manager.validate_path("../docs/test.md")
        
    with pytest.raises(ValueError):
        manager.validate_path("docs/../../etc/passwd")

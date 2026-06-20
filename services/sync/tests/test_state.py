"""StateManager 단위 테스트."""

import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from state import StateManager


class TestStateManager:
    def test_initial_state(self, tmp_path):
        sm = StateManager(str(tmp_path / "state"))
        assert sm.get_last_successful_sha("repo-a") is None

    def test_update_and_get(self, tmp_path):
        sm = StateManager(str(tmp_path / "state"))
        sm.update_last_successful_sha("repo-a", "abc123")
        assert sm.get_last_successful_sha("repo-a") == "abc123"

    def test_persistence(self, tmp_path):
        state_dir = str(tmp_path / "state")
        sm1 = StateManager(state_dir)
        sm1.update_last_successful_sha("repo-a", "sha1")

        sm2 = StateManager(state_dir)
        assert sm2.get_last_successful_sha("repo-a") == "sha1"

    def test_multiple_repos(self, tmp_path):
        sm = StateManager(str(tmp_path / "state"))
        sm.update_last_successful_sha("repo-a", "sha-a")
        sm.update_last_successful_sha("repo-b", "sha-b")
        assert sm.get_last_successful_sha("repo-a") == "sha-a"
        assert sm.get_last_successful_sha("repo-b") == "sha-b"

    def test_overwrite(self, tmp_path):
        sm = StateManager(str(tmp_path / "state"))
        sm.update_last_successful_sha("repo-a", "sha1")
        sm.update_last_successful_sha("repo-a", "sha2")
        assert sm.get_last_successful_sha("repo-a") == "sha2"

    def test_backup_file_created(self, tmp_path):
        state_dir = tmp_path / "state"
        sm = StateManager(str(state_dir))
        sm.update_last_successful_sha("repo-a", "sha1")
        sm.update_last_successful_sha("repo-a", "sha2")
        bak = state_dir / "repo-a.bak"
        assert bak.exists()
        data = json.loads(bak.read_text())
        assert data["last_successful_sha"] == "sha1"

    def test_corrupt_file_falls_back_to_backup(self, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        (state_dir / "repo-a.json").write_text("not valid json")
        (state_dir / "repo-a.bak").write_text(json.dumps({"last_successful_sha": "sha-bak"}))
        sm = StateManager(str(state_dir))
        assert sm.get_last_successful_sha("repo-a") == "sha-bak"

    def test_corrupt_both_returns_none(self, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        (state_dir / "repo-a.json").write_text("invalid")
        (state_dir / "repo-a.bak").write_text("also invalid")
        sm = StateManager(str(state_dir))
        assert sm.get_last_successful_sha("repo-a") is None

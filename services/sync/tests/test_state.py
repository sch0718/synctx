"""StateManager 단위 테스트."""

import json

from state import StateManager, RepoState, SyncedFileState, PendingFailure


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
        (state_dir / "repo-a.bak").write_text(json.dumps({
            "schema_version": 1,
            "repo_id": "repo-a",
            "last_successful_sha": "sha-bak",
            "last_run_id": "",
            "synced_files": {},
            "pending_failures": [],
        }))
        sm = StateManager(str(state_dir))
        assert sm.get_last_successful_sha("repo-a") == "sha-bak"

    def test_corrupt_both_returns_none(self, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        (state_dir / "repo-a.json").write_text("invalid")
        (state_dir / "repo-a.bak").write_text("also invalid")
        sm = StateManager(str(state_dir))
        assert sm.get_last_successful_sha("repo-a") is None


class TestPerFileTracking:
    def test_file_changed_new_file(self, tmp_path):
        sm = StateManager(str(tmp_path / "state"))
        assert sm.is_file_changed("repo-a", "docs/test.md", "sha256abc") is True

    def test_file_not_changed(self, tmp_path):
        sm = StateManager(str(tmp_path / "state"))
        state = sm.get_state("repo-a")
        sm.record_file_synced(
            state, "docs/test.md", "sha256abc", "xwiki",
            "repo-a:docs/test.md", "xwiki:docs.test", "commit1", "2026-01-01T00:00:00Z",
        )
        sm.save_state("repo-a", state)
        assert sm.is_file_changed("repo-a", "docs/test.md", "sha256abc") is False

    def test_file_changed_after_content_update(self, tmp_path):
        sm = StateManager(str(tmp_path / "state"))
        state = sm.get_state("repo-a")
        sm.record_file_synced(
            state, "docs/test.md", "sha256abc", "xwiki",
            "repo-a:docs/test.md", "xwiki:docs.test", "commit1", "2026-01-01T00:00:00Z",
        )
        sm.save_state("repo-a", state)
        assert sm.is_file_changed("repo-a", "docs/test.md", "sha256xyz") is True

    def test_record_file_deleted(self, tmp_path):
        sm = StateManager(str(tmp_path / "state"))
        state = sm.get_state("repo-a")
        sm.record_file_synced(
            state, "docs/test.md", "sha256abc", "xwiki",
            "repo-a:docs/test.md", "xwiki:docs.test", "commit1", "2026-01-01T00:00:00Z",
        )
        sm.record_file_deleted(state, "docs/test.md")
        assert "docs/test.md" not in state.synced_files


class TestPendingFailures:
    def test_add_pending_failure(self, tmp_path):
        sm = StateManager(str(tmp_path / "state"))
        state = sm.get_state("repo-a")
        sm.add_pending_failure(state, "docs/broken.md", "mapping_error", "Unknown status: review")
        assert len(state.pending_failures) == 1
        assert state.pending_failures[0].error_type == "mapping_error"
        assert state.pending_failures[0].path == "docs/broken.md"

    def test_duplicate_failure_increments_count(self, tmp_path):
        sm = StateManager(str(tmp_path / "state"))
        state = sm.get_state("repo-a")
        sm.add_pending_failure(state, "docs/broken.md", "mapping_error", "msg1")
        sm.add_pending_failure(state, "docs/broken.md", "transient_error", "msg2")
        assert len(state.pending_failures) == 1
        assert state.pending_failures[0].attempt_count == 2
        assert state.pending_failures[0].error_type == "transient_error"

    def test_clear_pending_failure(self, tmp_path):
        sm = StateManager(str(tmp_path / "state"))
        state = sm.get_state("repo-a")
        sm.add_pending_failure(state, "docs/a.md", "auth_error", "401")
        sm.add_pending_failure(state, "docs/b.md", "transient_error", "timeout")
        sm.clear_pending_failure(state, "docs/a.md")
        assert len(state.pending_failures) == 1
        assert state.pending_failures[0].path == "docs/b.md"

    def test_state_persistence_with_failures(self, tmp_path):
        sm = StateManager(str(tmp_path / "state"))
        state = sm.get_state("repo-a")
        sm.record_file_synced(
            state, "docs/ok.md", "sha1", "xwiki",
            "repo-a:docs/ok.md", "xwiki:docs.ok", "c1", "2026-01-01T00:00:00Z",
        )
        sm.add_pending_failure(state, "docs/fail.md", "auth_error", "403 Forbidden")
        state.last_successful_sha = "abc"
        sm.save_state("repo-a", state)

        loaded = sm.get_state("repo-a")
        assert loaded.last_successful_sha == "abc"
        assert "docs/ok.md" in loaded.synced_files
        assert loaded.synced_files["docs/ok.md"].content_sha == "sha1"
        assert len(loaded.pending_failures) == 1
        assert loaded.pending_failures[0].error_type == "auth_error"


class TestMigration:
    def test_migrate_flat_state(self, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        (state_dir / "repo-a.json").write_text(json.dumps({"last_successful_sha": "old-sha"}))

        sm = StateManager(str(state_dir))
        state = sm.get_state("repo-a")
        assert state.last_successful_sha == "old-sha"
        assert state.schema_version == 1
        assert state.synced_files == {}

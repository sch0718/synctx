"""LeaseLock acquire/release 단위 테스트."""

import os
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from kubernetes import client
from kubernetes.client.rest import ApiException

import lease as lease_module
from lease import LeaseLock, LeaseLostError, repo_lock


NAMESPACE = "synctx"
REPO_ID = "repo-a"
LEASE_NAME = f"synctx-sync-{REPO_ID}"


@pytest.fixture
def api(monkeypatch):
    api = MagicMock()
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.0.0.1")
    monkeypatch.setattr(lease_module, "HAS_KUBERNETES", True)
    monkeypatch.setattr(lease_module.config, "load_incluster_config", lambda: None)
    monkeypatch.setattr(lease_module.client, "CoordinationV1Api", lambda: api)
    return api


def existing_lease(holder, renewed_seconds_ago, duration=300):
    return client.V1Lease(
        metadata=client.V1ObjectMeta(name=LEASE_NAME),
        spec=client.V1LeaseSpec(
            holder_identity=holder,
            lease_duration_seconds=duration,
            renew_time=datetime.now(timezone.utc) - timedelta(seconds=renewed_seconds_ago),
        ),
    )


def replaced_lease(api):
    api.replace_namespaced_lease.assert_called_once()
    name, namespace, body = api.replace_namespaced_lease.call_args.args
    assert (name, namespace) == (LEASE_NAME, NAMESPACE)
    return body


class TestLeaseAcquire:
    @pytest.mark.parametrize("holder", ["", None])
    def test_released_lease_with_recent_renew_time_is_acquired(self, api, holder):
        api.read_namespaced_lease.return_value = existing_lease(holder, renewed_seconds_ago=290)

        assert LeaseLock(NAMESPACE, REPO_ID, "sync-new").acquire() is True

        body = replaced_lease(api)
        assert body.spec.holder_identity == "sync-new"
        assert body.spec.lease_duration_seconds == 300
        assert (datetime.now(timezone.utc) - body.spec.renew_time).total_seconds() < 5

    def test_lease_held_by_other_and_not_expired_is_not_acquired(self, api):
        api.read_namespaced_lease.return_value = existing_lease("sync-old", renewed_seconds_ago=290)

        assert LeaseLock(NAMESPACE, REPO_ID, "sync-new").acquire() is False
        api.replace_namespaced_lease.assert_not_called()

    def test_expired_lease_held_by_other_is_acquired(self, api):
        api.read_namespaced_lease.return_value = existing_lease("sync-crashed", renewed_seconds_ago=310)

        assert LeaseLock(NAMESPACE, REPO_ID, "sync-new").acquire() is True
        assert replaced_lease(api).spec.holder_identity == "sync-new"

    def test_lease_held_by_self_is_acquired_without_replace(self, api):
        api.read_namespaced_lease.return_value = existing_lease("sync-new", renewed_seconds_ago=10)

        assert LeaseLock(NAMESPACE, REPO_ID, "sync-new").acquire() is True
        api.replace_namespaced_lease.assert_not_called()

    def test_missing_lease_is_created(self, api):
        api.read_namespaced_lease.side_effect = ApiException(status=404)

        assert LeaseLock(NAMESPACE, REPO_ID, "sync-new").acquire() is True

        namespace, body = api.create_namespaced_lease.call_args.args
        assert namespace == NAMESPACE
        assert body.metadata.name == LEASE_NAME
        assert body.spec.holder_identity == "sync-new"

    def test_lease_created_concurrently_is_not_acquired(self, api):
        api.read_namespaced_lease.side_effect = ApiException(status=404)
        api.create_namespaced_lease.side_effect = ApiException(status=409)

        assert LeaseLock(NAMESPACE, REPO_ID, "sync-new").acquire() is False


class TestLeaseRenew:
    def test_renew_updates_own_renew_time(self, api):
        api.read_namespaced_lease.return_value = existing_lease("sync-old", renewed_seconds_ago=200)

        assert LeaseLock(NAMESPACE, REPO_ID, "sync-old").renew() is True

        body = replaced_lease(api)
        assert body.spec.holder_identity == "sync-old"
        assert (datetime.now(timezone.utc) - body.spec.renew_time).total_seconds() < 5

    def test_renew_fails_when_holder_changed(self, api):
        api.read_namespaced_lease.return_value = existing_lease("sync-new", renewed_seconds_ago=10)

        assert LeaseLock(NAMESPACE, REPO_ID, "sync-old").renew() is False
        api.replace_namespaced_lease.assert_not_called()

    def test_renew_fails_when_lease_deleted(self, api):
        api.read_namespaced_lease.side_effect = ApiException(status=404)

        assert LeaseLock(NAMESPACE, REPO_ID, "sync-old").renew() is False

    def test_renew_raises_on_transient_error(self, api):
        api.read_namespaced_lease.side_effect = ApiException(status=500)

        with pytest.raises(ApiException):
            LeaseLock(NAMESPACE, REPO_ID, "sync-old").renew()


def wait_until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def is_lost(lock):
    try:
        lock.ensure_held()
        return False
    except LeaseLostError:
        return True


class TestLeaseRenewer:
    @pytest.fixture
    def stored(self, api):
        stored = existing_lease("sync-old", renewed_seconds_ago=0)
        api.read_namespaced_lease.side_effect = lambda *args: stored
        api.replace_namespaced_lease.side_effect = lambda name, ns, body: body
        return stored

    def start(self, duration=0.3):
        lock = LeaseLock(NAMESPACE, REPO_ID, "sync-old", duration_seconds=duration)
        lock.start_renewing()
        return lock

    def test_renews_periodically_while_held(self, api, stored):
        lock = self.start()
        try:
            assert wait_until(lambda: api.replace_namespaced_lease.call_count >= 3)
            lock.ensure_held()
        finally:
            lock.stop_renewing()

    def test_stop_renewing_ends_renewal(self, api, stored):
        lock = self.start()
        assert wait_until(lambda: api.replace_namespaced_lease.call_count >= 1)
        lock.stop_renewing()
        count = api.replace_namespaced_lease.call_count

        time.sleep(0.3)

        assert api.replace_namespaced_lease.call_count == count

    def test_holder_change_marks_lease_lost(self, api, stored):
        stored.spec.holder_identity = "sync-new"
        lock = self.start()
        try:
            assert wait_until(lambda: is_lost(lock))
            api.replace_namespaced_lease.assert_not_called()
        finally:
            lock.stop_renewing()

    def test_transient_errors_past_duration_mark_lease_lost(self, api):
        api.read_namespaced_lease.side_effect = ApiException(status=500)
        lock = self.start()
        try:
            assert wait_until(lambda: is_lost(lock))
        finally:
            lock.stop_renewing()

    def test_transient_error_then_success_keeps_lease(self, api, stored):
        failures = iter([ApiException(status=500)])

        def read(*args):
            error = next(failures, None)
            if error:
                raise error
            return stored

        api.read_namespaced_lease.side_effect = read
        lock = self.start(duration=0.6)
        try:
            assert wait_until(lambda: api.replace_namespaced_lease.call_count >= 2)
            lock.ensure_held()
        finally:
            lock.stop_renewing()

    def test_lease_not_renewed_within_duration_is_lost(self, api, stored):
        lock = LeaseLock(NAMESPACE, REPO_ID, "sync-old", duration_seconds=0.1)
        lock.start_renewing()
        lock.stop_renewing()

        time.sleep(0.15)

        assert is_lost(lock)


class TestRepoLock:
    def test_yields_none_when_not_acquired(self, api):
        api.read_namespaced_lease.return_value = existing_lease("sync-other", renewed_seconds_ago=10)

        with repo_lock(NAMESPACE, REPO_ID, "sync-new") as lock:
            assert lock is None
        api.replace_namespaced_lease.assert_not_called()

    def test_renews_while_held_and_releases_on_exit(self, api):
        stored = existing_lease("sync-new", renewed_seconds_ago=0)
        api.read_namespaced_lease.side_effect = lambda *args: stored
        api.replace_namespaced_lease.side_effect = lambda name, ns, body: body

        with repo_lock(NAMESPACE, REPO_ID, "sync-new") as lock:
            lock.ensure_held()
            renewer = lock._renewer
            assert renewer.is_alive()

        assert not renewer.is_alive()
        assert stored.spec.holder_identity == ""


class TestLeaseRelease:
    def test_release_clears_own_holder(self, api):
        api.read_namespaced_lease.return_value = existing_lease("sync-old", renewed_seconds_ago=10)

        LeaseLock(NAMESPACE, REPO_ID, "sync-old").release()

        assert replaced_lease(api).spec.holder_identity == ""

    def test_release_keeps_lease_held_by_other(self, api):
        api.read_namespaced_lease.return_value = existing_lease("sync-other", renewed_seconds_ago=10)

        LeaseLock(NAMESPACE, REPO_ID, "sync-old").release()

        api.replace_namespaced_lease.assert_not_called()

    def test_next_run_acquires_right_after_release(self, api):
        stored = existing_lease("sync-old", renewed_seconds_ago=0)
        api.read_namespaced_lease.side_effect = lambda *args: stored
        api.replace_namespaced_lease.side_effect = lambda name, ns, body: body

        LeaseLock(NAMESPACE, REPO_ID, "sync-old").release()

        assert LeaseLock(NAMESPACE, REPO_ID, "sync-new").acquire() is True
        assert stored.spec.holder_identity == "sync-new"


class TestFileLock:
    @pytest.fixture
    def make_lock(self, tmp_path, monkeypatch):
        monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
        path = str(tmp_path / f"{LEASE_NAME}.lock")

        def make(identity):
            lock = LeaseLock(NAMESPACE, REPO_ID, identity)
            lock.lock_file = path
            return lock

        return make

    def test_held_lock_is_not_acquired_by_other(self, make_lock):
        assert make_lock("sync-old").acquire() is True
        assert make_lock("sync-new").acquire() is False

    def test_released_lock_is_acquired_immediately(self, make_lock):
        old = make_lock("sync-old")
        assert old.acquire() is True
        old.release()

        assert make_lock("sync-new").acquire() is True

    def test_stale_lock_is_acquired(self, make_lock):
        assert make_lock("sync-crashed").acquire() is True
        stale = time.time() - 310
        os.utime(make_lock("sync-crashed").lock_file, (stale, stale))

        assert make_lock("sync-new").acquire() is True

    def test_renew_touches_own_lock_file(self, make_lock):
        lock = make_lock("sync-old")
        assert lock.acquire() is True
        old = time.time() - 200
        os.utime(lock.lock_file, (old, old))

        assert lock.renew() is True
        assert time.time() - os.stat(lock.lock_file).st_mtime < 5

    def test_renew_fails_when_lock_taken_by_other(self, make_lock):
        assert make_lock("sync-crashed").acquire() is True
        stale = time.time() - 310
        os.utime(make_lock("sync-crashed").lock_file, (stale, stale))
        assert make_lock("sync-new").acquire() is True

        assert make_lock("sync-crashed").renew() is False

    def test_renew_fails_when_lock_file_removed(self, make_lock):
        lock = make_lock("sync-old")
        assert lock.acquire() is True
        os.unlink(lock.lock_file)

        assert lock.renew() is False

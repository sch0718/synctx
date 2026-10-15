"""LeaseLock acquire/release 단위 테스트."""

import os
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from kubernetes import client
from kubernetes.client.rest import ApiException

import lease as lease_module
from lease import LeaseLock


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

"""Kubernetes Lease 기반 per-repo lock 구현."""

import os
import time
import logging
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Generator, Optional

try:
    from kubernetes import client, config
    from kubernetes.client.rest import ApiException
    HAS_KUBERNETES = True
except ImportError:
    HAS_KUBERNETES = False

logger = logging.getLogger(__name__)


class LeaseLostError(RuntimeError):
    """동기화 도중 lease를 더 이상 보유하지 않음."""


class LeaseLock:
    def __init__(self, namespace: str, repo_id: str, identity: str, duration_seconds: int = 300):
        self.namespace = namespace
        self.repo_id = repo_id
        self.name = f"synctx-sync-{repo_id}"
        self.identity = identity
        self.duration_seconds = duration_seconds
        
        self.use_k8s = HAS_KUBERNETES and "KUBERNETES_SERVICE_HOST" in os.environ
        if self.use_k8s:
            try:
                config.load_incluster_config()
                self.api = client.CoordinationV1Api()
            except Exception as e:
                logger.warning(f"Failed to load incluster config: {e}. Falling back to file lock.")
                self.use_k8s = False

        # File lock fallback for local development
        self.lock_file = f"/tmp/{self.name}.lock"

        self._renewed_at = 0.0
        self._lost = threading.Event()
        self._stop = threading.Event()
        self._renewer: Optional[threading.Thread] = None

    def acquire(self) -> bool:
        if not self.use_k8s:
            return self._acquire_file_lock()

        try:
            lease = self.api.read_namespaced_lease(self.name, self.namespace)
            now = time.time()
            renew_time = lease.spec.renew_time.timestamp() if lease.spec.renew_time else 0

            if lease.spec.holder_identity == self.identity:
                return True

            released = not lease.spec.holder_identity
            expired = now > renew_time + (lease.spec.lease_duration_seconds or 0)
            if released or expired:
                lease.spec.holder_identity = self.identity
                lease.spec.renew_time = datetime.now(timezone.utc)
                lease.spec.lease_duration_seconds = self.duration_seconds
                self.api.replace_namespaced_lease(self.name, self.namespace, lease)
                return True
            return False

        except ApiException as e:
            if e.status == 404:
                lease = client.V1Lease(
                    metadata=client.V1ObjectMeta(name=self.name),
                    spec=client.V1LeaseSpec(
                        holder_identity=self.identity,
                        lease_duration_seconds=self.duration_seconds,
                        renew_time=datetime.now(timezone.utc)
                    )
                )
                try:
                    self.api.create_namespaced_lease(self.namespace, lease)
                    return True
                except ApiException as create_e:
                    if create_e.status == 409:
                        return False
                    logger.error(f"Failed to create lease {self.name}: {create_e}")
                    return False
            logger.error(f"Failed to acquire lease {self.name}: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error acquiring lease {self.name}: {e}")
            return False

    def renew(self) -> bool:
        """보유 중인 lease의 renew_time을 갱신. 다른 실행이 가져갔거나 사라졌으면 False."""
        if not self.use_k8s:
            return self._renew_file_lock()

        try:
            lease = self.api.read_namespaced_lease(self.name, self.namespace)
        except ApiException as e:
            if e.status == 404:
                return False
            raise

        if lease.spec.holder_identity != self.identity:
            return False
        lease.spec.renew_time = datetime.now(timezone.utc)
        self.api.replace_namespaced_lease(self.name, self.namespace, lease)
        return True

    def start_renewing(self) -> None:
        self._renewed_at = time.monotonic()
        self._renewer = threading.Thread(target=self._renew_loop, name=f"{self.name}-renewer", daemon=True)
        self._renewer.start()

    def stop_renewing(self) -> None:
        self._stop.set()
        if self._renewer:
            self._renewer.join()

    def ensure_held(self) -> None:
        if self._lost.is_set() or time.monotonic() - self._renewed_at >= self.duration_seconds:
            raise LeaseLostError(f"Lease {self.name} is no longer held by {self.identity}")

    def _renew_loop(self) -> None:
        interval = self.duration_seconds / 3
        while not self._stop.wait(interval):
            try:
                if self.renew():
                    self._renewed_at = time.monotonic()
                    continue
                logger.error(f"Lease {self.name} was taken over or removed, stopping sync")
                self._lost.set()
                return
            except Exception as e:
                logger.warning(f"Failed to renew lease {self.name}: {e}")

            if time.monotonic() - self._renewed_at + interval >= self.duration_seconds:
                logger.error(f"Lease {self.name} could not be renewed before expiry, stopping sync")
                self._lost.set()
                return

    def release(self) -> None:
        if not self.use_k8s:
            self._release_file_lock()
            return

        try:
            lease = self.api.read_namespaced_lease(self.name, self.namespace)
            if lease.spec.holder_identity == self.identity:
                # Clear identity to release
                lease.spec.holder_identity = ""
                self.api.replace_namespaced_lease(self.name, self.namespace, lease)
        except ApiException as e:
            if e.status != 404:
                logger.error(f"Failed to release lease: {e}")

    def _acquire_file_lock(self) -> bool:
        try:
            if os.path.exists(self.lock_file):
                # Simple stale check (5 mins)
                if time.time() - os.stat(self.lock_file).st_mtime > self.duration_seconds:
                    os.unlink(self.lock_file)
                else:
                    return False
            fd = os.open(self.lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(self.identity)
            return True
        except FileExistsError:
            return False
        except OSError:
            return False

    def _renew_file_lock(self) -> bool:
        try:
            with open(self.lock_file, "r") as f:
                if f.read().strip() != self.identity:
                    return False
            os.utime(self.lock_file)
            return True
        except FileNotFoundError:
            return False

    def _release_file_lock(self) -> None:
        try:
            if os.path.exists(self.lock_file):
                with open(self.lock_file, "r") as f:
                    if f.read().strip() == self.identity:
                        os.unlink(self.lock_file)
        except OSError:
            pass

@contextmanager
def repo_lock(namespace: str, repo_id: str, identity: str) -> Generator[Optional[LeaseLock], None, None]:
    """Per-repo lock을 획득해 보유하는 동안 주기적으로 갱신. 획득하지 못하면 None."""
    lock = LeaseLock(namespace, repo_id, identity)
    if not lock.acquire():
        yield None
        return
    lock.start_renewing()
    try:
        yield lock
    finally:
        lock.stop_renewing()
        lock.release()

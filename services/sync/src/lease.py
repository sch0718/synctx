"""Kubernetes Lease 기반 per-repo lock 구현."""

import os
import time
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Generator

try:
    from kubernetes import client, config
    from kubernetes.client.rest import ApiException
    HAS_KUBERNETES = True
except ImportError:
    HAS_KUBERNETES = False

logger = logging.getLogger(__name__)

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

    def acquire(self) -> bool:
        if not self.use_k8s:
            return self._acquire_file_lock()

        try:
            lease = self.api.read_namespaced_lease(self.name, self.namespace)
            now = time.time()
            renew_time = lease.spec.renew_time.timestamp() if lease.spec.renew_time else 0

            if lease.spec.holder_identity == self.identity:
                return True

            if now > renew_time + (lease.spec.lease_duration_seconds or 0):
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

    def _release_file_lock(self) -> None:
        try:
            if os.path.exists(self.lock_file):
                with open(self.lock_file, "r") as f:
                    if f.read().strip() == self.identity:
                        os.unlink(self.lock_file)
        except OSError:
            pass

@contextmanager
def repo_lock(namespace: str, repo_id: str, identity: str) -> Generator[bool, None, None]:
    """Context manager for acquiring and releasing a per-repo lock."""
    lock = LeaseLock(namespace, repo_id, identity)
    acquired = lock.acquire()
    try:
        yield acquired
    finally:
        if acquired:
            lock.release()

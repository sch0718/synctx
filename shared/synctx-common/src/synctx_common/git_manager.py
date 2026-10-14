"""Git 저장소 관리 모듈."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import re
import subprocess
from typing import Literal

from .config import RepoProfile
from .auth import git_token_auth_header, read_secret

logger = logging.getLogger(__name__)


RepoState = Literal["ready", "cloning", "error"]


@dataclass
class TreeEntry:
    name: str
    path: str
    type: Literal["file", "dir"]


@dataclass
class CommitInfo:
    sha: str
    author: str
    authored_at: str


class GitRepoManager:
    """하나의 저장소 프로필에 대한 git 작업을 관리한다."""

    def __init__(self, profile: RepoProfile, base_dir: str):
        self.profile = profile
        self.base_dir = Path(base_dir)
        self.repo_dir = self.base_dir / profile.id
        self._status: RepoState = "cloning"
        self._last_sync_at: datetime | None = None
        self._last_sync_source: str | None = None
        self._head_sha = ""
        self._last_error: str | None = None

    @property
    def status(self) -> RepoState:
        return self._status

    @property
    def last_sync_at(self) -> datetime | None:
        return self._last_sync_at

    @property
    def last_sync_source(self) -> str | None:
        return self._last_sync_source

    @property
    def head_sha_value(self) -> str:
        return self._head_sha

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def _sanitize_git_text(self, text: str) -> str:
        masked = re.sub(r"(https?://)([^/@:\s]+):([^@/\s]+)@", r"\1\2:***@", text)
        masked = re.sub(
            r"(?i)(Authorization:\s*(?:Bearer|Basic|token)\s+)\S+",
            r"\1***",
            masked,
        )
        return masked

    def _git_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        if self.profile.auth.type == "ssh" and self.profile.auth.ssh_key_file:
            env["GIT_SSH_COMMAND"] = (
                f"ssh -i {self.profile.auth.ssh_key_file} "
                "-o IdentitiesOnly=yes -o StrictHostKeyChecking=no"
            )
        return env

    def _git_config_args(self) -> list[str]:
        # core.quotepath=false: 비ASCII(한글 등) 경로를 octal escape 없이 UTF-8 그대로 출력.
        args: list[str] = ["-c", "core.quotepath=false"]
        if self.profile.auth.type != "token":
            return args
        pat_args_empty = args  # auth 부재 시에도 core.quotepath 는 유지
        try:
            token = read_secret(
                credential_env=self.profile.auth.credential_env,
                credential_file=self.profile.auth.credential_file,
            )
        except OSError as exc:
            logger.warning("token_credential_read_failed repo_id=%s error=%s", self.profile.id, exc)
            return pat_args_empty
        if not token:
            if self.profile.auth.credential_env:
                logger.warning(
                    "token_credential_env_missing repo_id=%s env=%s",
                    self.profile.id,
                    self.profile.auth.credential_env,
                )
            return pat_args_empty
        return [*args, "-c", f"http.extraHeader={git_token_auth_header(token)}"]

    def _run_git(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                ["git", *self._git_config_args(), *args],
                cwd=str(cwd or self.repo_dir),
                env=self._git_env(),
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as exc:
            stderr = self._sanitize_git_text((exc.stderr or b"").decode("utf-8", errors="replace"))
            logger.warning(
                "git_command_failed repo_id=%s args=%s stderr=%s",
                self.profile.id,
                " ".join(args),
                stderr.strip(),
            )
            raise

    def _clone_with_args(self, clone_args: list[str]) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        if self.repo_dir.exists():
            if (self.repo_dir / ".git").exists():
                return
            import shutil
            shutil.rmtree(self.repo_dir)
        self._run_git("clone", *clone_args, self.profile.url, str(self.repo_dir), cwd=self.base_dir)
        if self.profile.sparse_paths:
            self._run_git("sparse-checkout", "init", "--cone")
            self._run_git("sparse-checkout", "set", *self.profile.sparse_paths)
            self._run_git("checkout", self.profile.default_ref)

    def _clone(self) -> None:
        mode = self.profile.storage_mode
        if mode == "full":
            self._clone_with_args(["--single-branch", "--branch", self.profile.default_ref])
            return
        if mode == "shallow":
            depth = str(self.profile.shallow_depth or 1)
            self._clone_with_args(
                ["--single-branch", "--branch", self.profile.default_ref, "--depth", depth]
            )
            return

        filter_arg = "--filter=blob:none" if mode == "blobless" else "--filter=tree:0"
        try:
            self._clone_with_args(
                [
                    "--single-branch",
                    "--branch",
                    self.profile.default_ref,
                    "--no-checkout",
                    filter_arg,
                ]
            )
        except subprocess.CalledProcessError:
            depth = str(self.profile.shallow_depth or 1)
            self._clone_with_args(
                ["--single-branch", "--branch", self.profile.default_ref, "--depth", depth]
            )

    async def ensure(self) -> None:
        try:
            if not (self.repo_dir / ".git").exists():
                self._status = "cloning"
                self._clone()
            else:
                self._run_git("fetch", "origin", self.profile.default_ref)
            self._head_sha = self.head_sha()
            self._status = "ready"
            self._last_sync_at = datetime.now(timezone.utc)
            self._last_sync_source = "startup"
            self._last_error = None
        except subprocess.CalledProcessError:
            self._status = "error"
            self._last_error = "startup sync failed"

    async def fetch(self, source: str = "manual") -> str:
        try:
            self._run_git("fetch", "origin", self.profile.default_ref)
            self._head_sha = self.head_sha()
            self._status = "ready"
            self._last_sync_at = datetime.now(timezone.utc)
            self._last_sync_source = source
            self._last_error = None
            return self._head_sha
        except subprocess.CalledProcessError:
            self._status = "error"
            self._last_error = "fetch failed"
            raise

    def invalidate_cache(self) -> None:
        return

    def list_branches(self) -> list[str]:
        """원격 브랜치 목록을 반환한다."""
        result = self._run_git("ls-remote", "--heads", "origin")
        branches: list[str] = []
        for line in result.stdout.decode("utf-8").strip().splitlines():
            if not line:
                continue
            parts = line.split("\t", 1)
            if len(parts) == 2:
                branches.append(parts[1].replace("refs/heads/", ""))
        return sorted(branches)

    def _default_remote_ref(self) -> str:
        # fetch 는 refs/remotes/origin/<ref> 만 갱신하고 로컬 브랜치는 clone 시점에 머문다.
        return f"origin/{self.profile.default_ref}"

    def _resolve_ref(self, ref: str | None = None) -> str:
        """ref를 git 명령에 사용할 수 있는 형태로 변환한다."""
        if ref is None or ref == self.profile.default_ref:
            return self._default_remote_ref()
        try:
            self._run_git(
                "fetch", "origin",
                f"refs/heads/{ref}:refs/remotes/origin/{ref}",
            )
        except subprocess.CalledProcessError:
            logger.warning("fetch_ref_failed repo_id=%s ref=%s", self.profile.id, ref)
            raise
        return f"origin/{ref}"

    def validate_path(self, path: str) -> str:
        """경로 탐색(Path Traversal) 공격을 방어하고 정규화된 경로를 반환한다."""
        normalized = os.path.normpath(path.strip("/"))
        if normalized.startswith("..") or normalized.startswith("/"):
            raise ValueError(f"Invalid path: {path}")
        return normalized

    def list_tree(self, path: str = "", recursive: bool = False, ref: str | None = None) -> list[TreeEntry]:
        target = self.validate_path(path) if path else ""
        resolved = self._resolve_ref(ref)
        tree_spec = resolved if not target else f"{resolved}:{target}"
        args = ["ls-tree"]
        if recursive:
            args.append("-r")
        args.append(tree_spec)
        result = self._run_git(*args)
        entries: list[TreeEntry] = []
        for line in result.stdout.decode("utf-8").splitlines():
            if not line:
                continue
            left, full_path = line.split("\t", 1)
            if target and "/" not in full_path and not full_path.startswith(target + "/"):
                full_path = f"{target}/{full_path}"
            meta = left.split()
            item_type = "dir" if meta[1] == "tree" else "file"
            name = Path(full_path).name
            if name.startswith("."):
                continue
            entries.append(TreeEntry(name=name, path=full_path, type=item_type))
        return entries

    def file_paths(self, ref: str | None = None) -> set[str]:
        """저장소의 모든 파일 경로. list_tree와 달리 숨김 파일을 거르지 않고, -z로 특수 문자 경로도 그대로 읽는다."""
        result = self._run_git("ls-tree", "-r", "-z", "--name-only", self._resolve_ref(ref))
        return {path for path in result.stdout.decode("utf-8").split("\0") if path}

    def resolve_ref(self, ref: str | None = None) -> str:
        """여러 파일을 같은 ref로 읽을 때 fetch를 한 번만 하도록 해석 결과를 돌려준다."""
        return self._resolve_ref(ref)

    def read_file(self, path: str, ref: str | None = None) -> bytes:
        return self.read_resolved(path, self._resolve_ref(ref))

    def read_resolved(self, path: str, resolved: str) -> bytes:
        target = self.validate_path(path)
        if self.profile.storage_mode in {"blobless", "treeless"}:
            logger.info("lazy_blob_fetch repo_id=%s path=%s", self.profile.id, target)
        result = self._run_git("show", f"{resolved}:{target}")
        return result.stdout

    def read_file_text(self, path: str, ref: str | None = None) -> str:
        return self.read_file(path, ref=ref).decode("utf-8")

    def file_size(self, path: str, ref: str | None = None) -> int:
        """파일 내용을 로드하지 않고 바이트 크기만 반환 (git cat-file -s)."""
        target = self.validate_path(path)
        resolved = self._resolve_ref(ref)
        result = self._run_git("cat-file", "-s", f"{resolved}:{target}")
        return int(result.stdout.decode("utf-8").strip())

    def last_commit(self, path: str) -> CommitInfo:
        target = self.validate_path(path)
        result = self._run_git(
            "log", "-1", "--format=%H%x1f%an%x1f%aI", self._default_remote_ref(), "--", target
        )
        sha, author, authored_at = result.stdout.decode("utf-8").strip().split("\x1f")
        return CommitInfo(sha=sha, author=author, authored_at=authored_at)

    def head_sha(self) -> str:
        result = self._run_git("rev-parse", self._default_remote_ref())
        return result.stdout.decode("utf-8").strip()

    def diff_since(self, since_sha: str) -> list[tuple[str, str]]:
        """특정 커밋 이후 변경된 파일 목록 반환.
        
        Returns:
            list of (status, file_path)
            status: A(Added), M(Modified), D(Deleted), R(Renamed) 등
        """
        # -M: detect renames
        result = self._run_git("diff", "--name-status", "-M", f"{since_sha}..{self._default_remote_ref()}")
        changes = []
        for line in result.stdout.decode("utf-8").splitlines():
            if not line:
                continue
            parts = line.split("\t")
            status = parts[0][0]  # R100 -> R
            if status == "R" and len(parts) >= 3:
                changes.append(("D", parts[1]))
                changes.append(("A", parts[2]))
            else:
                path = parts[-1]
                changes.append((status, path))
        return changes

    def commit_and_push(self, file_path: str, content: bytes, message: str, author_name: str = "synctx", author_email: str = "synctx@internal") -> str:
        """파일을 커밋하고 푸시한다 (Marp 출판 등에서 사용)."""
        target = self.validate_path(file_path)
        abs_path = self.repo_dir / target
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_bytes(content)
        
        self._run_git("add", target)
        
        env = self._git_env()
        env["GIT_AUTHOR_NAME"] = author_name
        env["GIT_AUTHOR_EMAIL"] = author_email
        env["GIT_COMMITTER_NAME"] = author_name
        env["GIT_COMMITTER_EMAIL"] = author_email
        
        subprocess.run(
            ["git", *self._git_config_args(), "commit", "-m", message],
            cwd=str(self.repo_dir),
            env=env,
            check=True,
            capture_output=True,
        )
        
        self._run_git("pull", "--rebase", "origin", self.profile.default_ref)
        self._run_git("push", "origin", self.profile.default_ref)
        return self.head_sha()

"""Slides sidecar / 인덱스 생성 스크립트 테스트."""

import base64
import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

SLIDES_DIR = Path(__file__).resolve().parent.parent
SIDECAR = SLIDES_DIR / "sync-sidecar.sh"
GENERATE_INDEX = SLIDES_DIR / "generate-index.sh"

REPO_URL = "https://git.example.internal/org/slides.git"
TOKEN = "secret-read-token"

FAKE_GIT = """#!/bin/sh
printf '%s\\n' "$*" >> "$GIT_CALL_LOG"
for arg in "$@"; do last="$arg"; done
case " $* " in
    *" clone "*) mkdir -p "$last/.git" ;;
esac
exit 0
"""


def _run_git(cwd: Path, *args: str, env: dict | None = None) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=env)


@pytest.fixture
def fake_git(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    git = bin_dir / "git"
    git.write_text(FAKE_GIT)
    git.chmod(0o755)
    return bin_dir, tmp_path / "git-calls.log"


def _run_sidecar(tmp_path, fake_git, *, url=REPO_URL, token=TOKEN, wait_for="fetch"):
    bin_dir, call_log = fake_git
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "GIT_CALL_LOG": str(call_log),
        "PUBLISH_REPO_URL": url,
        "GIT_READ_TOKEN": token,
        "POLL_INTERVAL_SECONDS": "30",
        "SLIDES_WORKTREE": str(tmp_path / "worktree"),
        "GENERATE_INDEX_SCRIPT": str(tmp_path / "missing-generate-index.sh"),
    }
    proc = subprocess.Popen(
        ["sh", str(SIDECAR)], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True
    )
    try:
        deadline = time.time() + 5
        while time.time() < deadline:
            if call_log.exists() and wait_for in call_log.read_text():
                break
            time.sleep(0.05)
    finally:
        os.killpg(proc.pid, signal.SIGTERM)
        output = proc.communicate(timeout=5)[0].decode()
    return call_log.read_text().splitlines(), output


def _expected_header(token: str) -> str:
    encoded = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return f"http.extraHeader=Authorization: Basic {encoded}"


class TestSidecarAuth:
    def test_clone_passes_token_as_header_not_url(self, tmp_path, fake_git):
        calls, output = _run_sidecar(tmp_path, fake_git, wait_for="clone")

        clone = next(c for c in calls if " clone " in f" {c} ")
        assert clone.startswith(f"-c {_expected_header(TOKEN)} clone ")
        assert f" {REPO_URL} " in clone
        assert all(TOKEN not in c for c in calls)
        assert TOKEN not in output

    def test_existing_worktree_remote_url_is_reset_and_fetch_uses_header(self, tmp_path, fake_git):
        (tmp_path / "worktree" / ".git").mkdir(parents=True)

        calls, _ = _run_sidecar(tmp_path, fake_git, wait_for="fetch")

        assert f"-C {tmp_path / 'worktree'} remote set-url origin {REPO_URL}" in calls
        fetch = next(c for c in calls if " fetch " in f" {c} ")
        assert fetch == f"-c {_expected_header(TOKEN)} fetch origin main"

    def test_non_https_url_does_not_send_header(self, tmp_path, fake_git):
        url = "http://git.example.internal/org/slides.git"

        calls, _ = _run_sidecar(tmp_path, fake_git, url=url, wait_for="clone")

        clone = next(c for c in calls if " clone " in f" {c} ")
        assert "extraHeader" not in clone
        assert f" {url} " in clone


class TestGenerateIndex:
    @pytest.fixture
    def slides_repo(self, tmp_path):
        repo = tmp_path / "repo"
        slides = repo / "_published" / "slides"
        slides.mkdir(parents=True)
        _run_git(repo, "init", "-q")
        return repo, slides

    def _commit(self, repo: Path, date: str) -> None:
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
            "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date,
        }
        _run_git(repo, "add", ".", env=env)
        _run_git(repo, "commit", "-qm", "publish", env=env)

    def _generate(self, slides: Path) -> str:
        subprocess.run(["sh", str(GENERATE_INDEX), str(slides)], check=True, capture_output=True)
        return (slides / "index.html").read_text()

    def test_filenames_are_html_escaped(self, slides_repo):
        repo, slides = slides_repo
        (slides / "<img src=x onerror=alert(1)>.html").write_text("x")
        (slides / "Q&A \"final\".html").write_text("x")
        self._commit(repo, "2025-01-15T10:00:00")

        html = self._generate(slides)

        assert '<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,' in html
        assert "<img src=x" not in html
        assert 'href="&lt;img%20src=x%20onerror=alert(1)&gt;.html"' in html
        assert '<div class="slide-title">&lt;img src=x onerror=alert(1)&gt;</div>' in html
        assert '<div class="slide-title">Q&amp;A &quot;final&quot;</div>' in html

    def test_url_special_characters_are_percent_encoded(self, slides_repo):
        repo, slides = slides_repo
        (slides / "회의#3.html").write_text("x")
        (slides / "draft?v=2 100%.html").write_text("x")
        self._commit(repo, "2025-01-15T10:00:00")

        html = self._generate(slides)

        assert 'href="회의%233.html"' in html
        assert 'href="draft%3Fv=2%20100%25.html"' in html

    def test_date_uses_last_commit_date(self, slides_repo):
        repo, slides = slides_repo
        (slides / "old-deck.html").write_text("x")
        self._commit(repo, "2025-01-15T10:00:00")

        html = self._generate(slides)

        assert "&middot; 2025-01-15" in html

    def test_untracked_file_falls_back_to_mtime(self, tmp_path):
        slides = tmp_path / "plain"
        slides.mkdir()
        deck = slides / "deck.html"
        deck.write_text("x")
        mtime = time.mktime(time.strptime("2024-03-05 12:00:00", "%Y-%m-%d %H:%M:%S"))
        os.utime(deck, (mtime, mtime))

        html = self._generate(slides)

        assert "&middot; 2024-03-05" in html

    def test_empty_directory_shows_empty_state(self, tmp_path):
        slides = tmp_path / "empty"
        slides.mkdir()

        html = self._generate(slides)

        assert "아직 발행된 프레젠테이션이 없습니다." in html

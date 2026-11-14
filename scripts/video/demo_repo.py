"""중립적인 시연 문서를 복사해 main·draft 브랜치가 있는 저장소를 만든다."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

SOURCE = Path(__file__).resolve().parent / "demo-docs"
PROJECT = SOURCE.parents[2]
MEETING = "회의/2026-11-주간-회의.md"
SYSTEM = "설계/시스템-구성.md"
MAIN_TEXT = "기본 대여 기간은 14일이며, 한 번에 3권까지 빌릴 수 있습니다."
DRAFT_TEXT = "검토 중인 변경안: 대여 기간을 21일로 늘리고, 한 번에 5권까지 빌리도록 합니다."
MARKER = "synctx-video-demo"


# 환경 변수의 작성자 설정이 -c user.name보다 우선하므로 직접 고정한다(화면의 「마지막 커밋」에 보인다).
GIT_ENV = {"GIT_AUTHOR_NAME": "시연 문서", "GIT_AUTHOR_EMAIL": "demo@example.invalid",
           "GIT_COMMITTER_NAME": "시연 문서", "GIT_COMMITTER_EMAIL": "demo@example.invalid"}


def git(destination: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(destination), "-c", "core.hooksPath=/dev/null",
         "-c", "commit.gpgsign=false", "-c", "user.name=시연 문서",
         "-c", "user.email=demo@example.invalid", *args],
        check=True, capture_output=True, text=True, env={**os.environ, **GIT_ENV},
    )


def create(destination: Path) -> Path:
    """이 도구가 만든 저장소만 다시 만들고, 다른 기존 폴더는 보존한다."""
    if destination.is_symlink():
        raise ValueError("시연 저장소 경로에 심볼릭 링크를 사용할 수 없습니다")
    destination = destination.resolve()
    if destination.is_relative_to(PROJECT) or PROJECT.is_relative_to(destination):
        raise ValueError("시연 저장소는 프로젝트 밖의 별도 작업 폴더에 두세요")
    if destination.exists():
        marker = destination / ".git" / MARKER
        if not marker.is_file() or marker.read_text(encoding="utf-8") != "시연 저장소\n":
            raise ValueError("기존 폴더를 덮어쓰지 않습니다. 비어 있는 새 경로를 지정하세요")
        shutil.rmtree(destination)
    shutil.copytree(SOURCE, destination)
    meeting = destination / MEETING
    starts_at = (datetime.now(UTC) - timedelta(minutes=10)).isoformat(timespec="seconds")
    meeting.write_text(meeting.read_text(encoding="utf-8").replace("__STARTS_AT__", starts_at), encoding="utf-8")
    git(destination, "init", "-b", "main")
    (destination / ".git" / MARKER).write_text("시연 저장소\n", encoding="utf-8")
    git(destination, "add", ".")
    git(destination, "commit", "-m", "도서 대여 시연 문서 추가")
    git(destination, "checkout", "-b", "draft")
    system = destination / SYSTEM
    original = system.read_text(encoding="utf-8")
    if MAIN_TEXT not in original:
        raise ValueError("브랜치 시연에 필요한 기본 문단을 찾지 못했습니다")
    system.write_text(original.replace(MAIN_TEXT, DRAFT_TEXT), encoding="utf-8")
    git(destination, "commit", "-am", "대여 기간과 한도 변경안 검토")
    git(destination, "checkout", "main")
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, help="시연 저장소를 만들 프로젝트 밖 경로")
    args = parser.parse_args()
    try:
        destination = create(args.destination)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"시연 저장소를 만들지 못했습니다: {exc}\n")
    print(f"시연 저장소: {destination}")


if __name__ == "__main__":
    main()

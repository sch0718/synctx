"""정적 내보내기 CLI: `python -m static_export build|verify`.

종료 코드: 성공 0, 검사·빌드 실패 1, 설정 오류 2. 메시지에는 토큰 값·예외 본문을 넣지 않는다.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from urllib.parse import quote

from static_export import build, crawl, verify
from static_export.config import SiteConfig, load_site_config

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_CONFIG = 2

DEFAULT_CONFIG = "static-export.yaml"
DEFAULT_OUT = "dist"
DEFAULT_CACHE = Path.home() / ".cache" / "synctx" / "static-export"
DEFAULT_TOKEN_ENV = "GIT_READ_TOKEN"


def select_repos(argument: str | None, environment: str | None) -> list[str] | None:
    """인자가 환경변수보다 우선한다. 비어 있으면 설정의 전체 저장소다."""
    raw = argument if argument is not None else environment
    ids = [item.strip() for item in (raw or "").split(",") if item.strip()]
    return ids or None


def secret_values(site: SiteConfig | None) -> list[str]:
    names = {DEFAULT_TOKEN_ENV}
    if site is not None:
        names |= {repo.auth["credential_env"] for repo in site.repositories if repo.auth.get("credential_env")}
    return [value for name in sorted(names) if (value := os.environ.get(name, "").strip())]


def exclude_markers(site: SiteConfig | None) -> list[str]:
    """제외 경로가 산출물의 본문 주소(`/view/…`)나 파일 경로(`view/…`)로 새면 잡도록 두 형태의 표지를 만든다."""
    if site is None:
        return []
    markers = []
    for repo in site.repositories:
        for prefix in repo.exclude:
            encoded = quote(prefix, safe="/")
            markers += [f"/view/{repo.id}/{encoded}", f"/raw/{repo.id}/{encoded}",
                        f"view/{repo.id}/{prefix}", f"raw/{repo.id}/{prefix}"]
    return markers


def _fail(message: str, code: int) -> int:
    print(f"오류: {message}", file=sys.stderr)
    return code


def _report_verify(out: Path, site: SiteConfig | None) -> int:
    violations = verify.verify_dist(out, secret_values=secret_values(site), exclude_markers=exclude_markers(site))
    if not violations:
        print("산출물 검사 통과")
        return EXIT_OK
    print(f"산출물 검사 실패: 위반 {len(violations)}건 (dist는 남겨 둔다)", file=sys.stderr)
    for violation in violations:
        print(f"  - {violation}", file=sys.stderr)
    return EXIT_FAILED


def _build(args: argparse.Namespace) -> int:
    selected = select_repos(args.repos, os.environ.get("STATIC_EXPORT_REPOS"))
    config_path = Path(args.config)
    try:
        site = load_site_config(config_path, selected)
    except ValueError as exc:
        return _fail(str(exc), EXIT_CONFIG)
    out = Path(args.out).expanduser()
    try:
        report = build.build_site(site, out, Path(args.cache_dir), config_path=config_path)
    except build.BuildConfigError as exc:
        return _fail(str(exc), EXIT_CONFIG)
    except crawl.CrawlError as exc:
        return _fail(f"빌드를 중단했습니다 — {exc}", EXIT_FAILED)
    except Exception as exc:
        return _fail(f"빌드 중 예기치 않은 오류가 났습니다({type(exc).__name__})", EXIT_FAILED)
    stages = ", ".join(f"{name} {seconds}초" for name, seconds in report.stage_seconds.items())
    print(f"빌드 완료: 페이지 {report.page_count}개, 파일 {report.file_count}개, {report.total_bytes}바이트 ({stages})")
    if report.skipped:
        print(f"건너뛴 항목 {len(report.skipped)}건:")
        for path, reason in report.skipped:
            print(f"  - {path}: {reason}")
    return _report_verify(out.resolve(), site)


def _verify(args: argparse.Namespace) -> int:
    site = None
    if args.config:
        try:
            site = load_site_config(Path(args.config))
        except ValueError as exc:
            return _fail(str(exc), EXIT_CONFIG)
    return _report_verify(Path(args.out).expanduser(), site)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="static_export", description="Viewer를 정적 사이트로 내보내고 산출물을 검사한다")
    commands = parser.add_subparsers(dest="command", required=True)
    build_cmd = commands.add_parser("build", help="저장소를 순회해 dist를 만들고 검사한다")
    build_cmd.add_argument("--config", default=DEFAULT_CONFIG, help=f"설정 파일(기본 {DEFAULT_CONFIG})")
    build_cmd.add_argument("--out", default=DEFAULT_OUT, help=f"출력 디렉터리(기본 {DEFAULT_OUT})")
    build_cmd.add_argument("--repos", default=None, help="내보낼 저장소 id(쉼표 구분). 없으면 환경변수 STATIC_EXPORT_REPOS, 그것도 없으면 전체")
    build_cmd.add_argument("--cache-dir", default=str(DEFAULT_CACHE), help="저장소 캐시 디렉터리")
    build_cmd.set_defaults(handler=_build)
    verify_cmd = commands.add_parser("verify", help="이미 만든 dist를 검사한다")
    verify_cmd.add_argument("--out", default=DEFAULT_OUT, help=f"검사할 디렉터리(기본 {DEFAULT_OUT})")
    verify_cmd.add_argument("--config", default=None, help="토큰 환경변수 이름과 제외 경로를 읽을 설정 파일(선택)")
    verify_cmd.set_defaults(handler=_verify)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())

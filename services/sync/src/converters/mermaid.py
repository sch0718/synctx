#!/usr/bin/env python3
"""마크다운 문서 내 Mermaid 코드 블록을 SVG 파일로 일괄 변환합니다."""

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

MERMAID_FENCE = re.compile(r"```mermaid\r?\n(.*?)\r?\n```", re.DOTALL)


def convert_mermaid_to_svg(mermaid_code: str) -> bytes:
    """Convert one Mermaid diagram to SVG bytes for Sync Engine attachment upload."""
    mmdc = find_mmdc(None)
    if not mmdc:
        raise RuntimeError("mmdc executable not found")
    with tempfile.TemporaryDirectory() as tmpdir:
        svg_path = Path(tmpdir) / "diagram.svg"
        ok = convert_block_to_svg(
            mermaid_code,
            svg_path,
            mmdc,
            theme="default",
            background="white",
            chrome_path=None,
        )
        if not ok:
            raise RuntimeError("Mermaid conversion failed")
        return svg_path.read_bytes()


def find_mmdc(override: Optional[str]) -> Optional[str]:
    if override:
        return override
    found = shutil.which("mmdc")
    if found:
        return found
    # npm global 설치 경로 후보
    for candidate in [
        Path.home() / ".npm-global" / "bin" / "mmdc",
        Path("/usr/local/bin/mmdc"),
        Path("/opt/homebrew/bin/mmdc"),
    ]:
        if candidate.exists():
            return str(candidate)
    return None


def mmdc_available(mmdc: str) -> bool:
    try:
        subprocess.run([mmdc, "--version"], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def convert_block_to_svg(
    mermaid_code: str,
    svg_path: Path,
    mmdc: str,
    theme: str,
    background: str,
    chrome_path: Optional[str] = None,
) -> bool:
    with tempfile.NamedTemporaryFile(
        suffix=".mmd", mode="w", encoding="utf-8", delete=False
    ) as f:
        f.write(mermaid_code)
        tmp = Path(f.name)
    try:
        cmd = [
            mmdc,
            "-i", str(tmp),
            "-o", str(svg_path),
            "--theme", theme,
            "--backgroundColor", background,
        ]
        env = None
        if chrome_path:
            import os
            env = {**os.environ, "PUPPETEER_EXECUTABLE_PATH": chrome_path}
        result = subprocess.run(cmd, capture_output=True, text=True, env=env)
        if result.returncode != 0:
            print(f"    [오류] {result.stderr.strip()}", file=sys.stderr)
            return False
        return True
    finally:
        tmp.unlink(missing_ok=True)


def read_svg_inline(svg_path: Path) -> str:
    """SVG 파일을 읽어 인라인 삽입용 문자열로 반환합니다."""
    text = svg_path.read_text(encoding="utf-8")
    # XML 선언 및 DOCTYPE 제거
    text = re.sub(r"<\?xml[^?]*\?>", "", text)
    text = re.sub(r"<!DOCTYPE[^>]*>", "", text)
    return text.strip()


def process_file(md_path: Path, args, mmdc: str, input_root: Path) -> int:
    content = md_path.read_text(encoding="utf-8")
    matches = list(MERMAID_FENCE.finditer(content))

    if not matches:
        print(f"[건너뜀] Mermaid 블록 없음: {md_path.name}")
        return 0

    print(f"[처리] {md_path.name} — {len(matches)}개 블록")

    if args.output:
        output_root = Path(args.output).expanduser().resolve()
        if input_root.is_dir():
            rel_parent = md_path.parent.relative_to(input_root)
            svg_dir = output_root / rel_parent
        else:
            svg_dir = output_root
    else:
        svg_dir = md_path.parent
    svg_dir.mkdir(parents=True, exist_ok=True)

    prefix = md_path.stem
    converted = 0
    replacements: list[tuple[str, str]] = []  # (원본 텍스트, 교체 텍스트)

    for i, match in enumerate(matches, start=1):
        code = match.group(1)
        svg_filename = f"{prefix}-mermaid-{i}.svg"
        svg_path = svg_dir / svg_filename

        print(f"  [{i}/{len(matches)}] → {svg_filename}", end=" ")
        ok = convert_block_to_svg(code, svg_path, mmdc, args.theme, args.background, args.chrome_path)
        if not ok:
            print("실패")
            replacements.append((match.group(0), match.group(0)))  # 원본 유지
            continue

        print("완료")
        converted += 1

        if args.inline:
            svg_content = read_svg_inline(svg_path)
            replacement = f"\n{svg_content}\n"
            if args.keep_svg_files is False:
                svg_path.unlink(missing_ok=True)
        elif args.replace:
            try:
                rel = svg_path.relative_to(md_path.parent)
            except ValueError:
                rel = svg_path
            replacement = f"![Mermaid Diagram {i}]({rel.as_posix()})"
        else:
            replacement = match.group(0)  # 교체 없음

        replacements.append((match.group(0), replacement))

    if args.inline or args.replace:
        new_content = content
        for original, replacement in replacements:
            new_content = new_content.replace(original, replacement, 1)

        if args.in_place:
            md_path.write_text(new_content, encoding="utf-8")
            print(f"  [업데이트] {md_path} (원본 파일 덮어씀)")
        else:
            out_md = md_path.with_stem(md_path.stem + args.suffix)
            out_md.write_text(new_content, encoding="utf-8")
            print(f"  [저장] {out_md}")

    return converted


def gather_md_files(input_path: Path, recursive: bool) -> list[Path]:
    if input_path.is_file():
        if input_path.suffix.lower() != ".md":
            raise ValueError("입력 파일은 .md 이어야 합니다.")
        return [input_path]
    pattern = "**/*.md" if recursive else "*.md"
    return sorted(p for p in input_path.glob(pattern) if p.is_file())


def parse_args():
    p = argparse.ArgumentParser(
        description="마크다운 내 Mermaid 블록을 SVG로 일괄 변환합니다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  # SVG 파일만 생성 (마크다운 수정 없음)
  python3 mermaid_to_svg.py --input doc.md

  # SVG 파일 생성 + 마크다운 코드 블록을 이미지 링크로 교체 (새 파일)
  python3 mermaid_to_svg.py --input doc.md --replace

  # 인라인 SVG로 교체 (새 파일)
  python3 mermaid_to_svg.py --input doc.md --inline

  # 원본 파일 직접 수정
  python3 mermaid_to_svg.py --input doc.md --replace --in-place

  # 디렉터리 내 모든 md 파일 재귀 처리
  python3 mermaid_to_svg.py --input ./docs --recursive --replace
""",
    )
    p.add_argument("--input", required=True, help="입력 .md 파일 또는 디렉터리")
    p.add_argument("--output", help="SVG 출력 디렉터리 (기본: 입력 파일과 같은 위치)")
    p.add_argument("--recursive", action="store_true", help="디렉터리 입력 시 하위 폴더까지 처리")
    p.add_argument(
        "--replace",
        action="store_true",
        help="마크다운 코드 블록을 ![...](*.svg) 이미지 링크로 교체",
    )
    p.add_argument(
        "--inline",
        action="store_true",
        help="마크다운 코드 블록을 인라인 SVG(<svg>...)로 교체",
    )
    p.add_argument(
        "--in-place",
        action="store_true",
        help="원본 .md 파일을 직접 수정 (기본: 접미사를 붙인 새 파일 생성)",
    )
    p.add_argument(
        "--suffix",
        default="-converted",
        help="교체 결과 파일 접미사 (기본: -converted, --in-place와 함께 사용 시 무시)",
    )
    p.add_argument(
        "--theme",
        choices=["default", "forest", "dark", "neutral", "base"],
        default="default",
        help="Mermaid 테마 (기본: default)",
    )
    p.add_argument(
        "--background",
        default="white",
        help="SVG 배경색 (기본: white, 투명은 transparent)",
    )
    p.add_argument(
        "--keep-svg-files",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="--inline 사용 시 SVG 파일 유지 여부 (기본: 유지, 비활성화: --no-keep-svg-files)",
    )
    p.add_argument("--mmdc", help="mmdc 실행 파일 경로 (기본: 자동 감지)")
    p.add_argument(
        "--chrome-path",
        dest="chrome_path",
        help="Chrome/Chromium 실행 파일 경로 (PUPPETEER_EXECUTABLE_PATH 환경 변수로 전달)",
    )
    return p.parse_args()


def main():
    args = parse_args()

    if args.inline and args.replace:
        print("[오류] --inline과 --replace는 동시에 사용할 수 없습니다.", file=sys.stderr)
        sys.exit(1)

    mmdc = find_mmdc(args.mmdc)
    if not mmdc or not mmdc_available(mmdc):
        print(
            "[오류] mmdc(Mermaid CLI)를 찾을 수 없습니다.\n"
            "  설치: npm install -g @mermaid-js/mermaid-cli\n"
            "  또는 --mmdc 옵션으로 경로를 직접 지정하세요.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"[mmdc] {mmdc}")

    input_path = Path(args.input).expanduser().resolve()
    if not input_path.exists():
        print(f"[오류] 입력 경로가 없습니다: {input_path}", file=sys.stderr)
        sys.exit(1)

    md_files = gather_md_files(input_path, args.recursive)
    if not md_files:
        print(f"[오류] 변환할 .md 파일이 없습니다: {input_path}", file=sys.stderr)
        sys.exit(1)

    total_files = len(md_files)
    total_converted = 0
    for md_path in md_files:
        total_converted += process_file(md_path, args, mmdc, input_path)

    print(f"\n완료: 파일 {total_files}개 처리, SVG {total_converted}개 생성")


if __name__ == "__main__":
    main()

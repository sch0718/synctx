"""Sync Engine 첨부용 Mermaid SVG 변환."""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

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

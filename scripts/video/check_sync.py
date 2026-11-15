#!/usr/bin/env python3
"""녹화 시각과 대본·문장 음성이 일치하는지 확인한다."""

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def media_path(value, base):
    path = Path(value)
    return path if path.is_absolute() else base / path


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def duration(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        check=True, capture_output=True, text=True,
    )
    value = float(result.stdout.strip())
    if not math.isfinite(value) or value <= 0:
        raise ValueError("음성 길이가 올바르지 않습니다")
    return value


def check_sync(out, scenes_path=HERE / "scenes.json"):
    """문제를 출력하고 개수를 반환한다. 상대 미디어 경로는 목록 파일 기준이다."""
    marks = read_json(out / "marks.json")
    voice = read_json(out / "voice" / "lines.json")
    script = read_json(scenes_path)["scenes"]
    problems = []

    def bad(message):
        problems.append(message)
        print(f"문제: {message}", file=sys.stderr)

    expected_scenes = [scene["key"] for scene in script]
    expected_lines = [(scene["key"], i) for scene in script for i in range(len(scene["lines"]))]
    scene_marks = marks["scenes"]
    line_marks = marks["lines"]
    if [scene.get("scene") for scene in scene_marks] != expected_scenes:
        bad("장면 개수 또는 순서가 scenes.json과 다릅니다")
    if [(line.get("scene"), line.get("i")) for line in line_marks] != expected_lines:
        bad("문장 개수 또는 순서가 scenes.json과 다릅니다")
    if [(line.get("scene"), line.get("i")) for line in voice] != expected_lines:
        bad("음성 목록의 문장 개수 또는 순서가 scenes.json과 다릅니다")
    original = {(scene["key"], i): text for scene in script for i, text in enumerate(scene["lines"])}
    voices = {(line.get("scene"), line.get("i")): line for line in voice}
    end = marks.get("end")
    valid_end = number(end) and end > 0
    if not valid_end:
        bad("녹화 종료 시각이 올바르지 않습니다")
    previous_scene = -1
    for scene in scene_marks:
        key = scene.get("scene")
        start, ready = scene.get("t"), scene.get("ready")
        if not number(start) or start < 0 or start < previous_scene or (valid_end and start >= end):
            bad(f"{key}: 장면 시작 시각이 올바르지 않습니다")
        else:
            previous_scene = start
        if not number(ready):
            bad(f"{key}: ready 기록이 없거나 올바르지 않습니다")
            continue
        if ready < 0 or (number(start) and ready < start) or (valid_end and ready > end):
            bad(f"{key}: ready가 장면 범위를 벗어납니다")
        first = next((line for line in line_marks if line.get("scene") == key and line.get("i") == 0), None)
        if first is None or not number(first.get("t")):
            bad(f"{key}: 첫 문장 시각이 없습니다")
        else:
            lag = ready - first["t"]
            print(f"{key}: 화면 준비와 첫 문장 차이 {lag:+.2f}초")
            if abs(lag) > 1:
                bad(f"{key}: 화면 준비와 첫 문장 차이가 1초를 넘습니다")

    previous_end = 0
    for line in line_marks:
        key = (line.get("scene"), line.get("i"))
        label = f"{key[0]}-{key[1]}"
        start, length = line.get("t"), line.get("dur")
        valid_time = number(start) and start >= 0 and number(length) and length > 0
        if not valid_time:
            bad(f"{label}: 문장 시각 또는 길이가 올바르지 않습니다")
        else:
            if start < previous_end - 0.000001:
                bad(f"{label}: 앞 문장과 겹치거나 녹화 순서가 뒤바뀌었습니다")
            previous_end = max(previous_end, start + length)
            if valid_end and start + length > end + 0.000001:
                bad(f"{label}: 음성이 녹화 종료 시각을 넘습니다")
        item = voices.get(key)
        if item is None:
            bad(f"{label}: 음성 목록에 없습니다")
            continue
        if item.get("t") != original.get(key):
            bad(f"{label}: 음성 원문이 scenes.json과 다릅니다")
        try:
            path = media_path(item["file"], out / "voice")
            if path.suffix.lower() != ".wav" or not path.is_file():
                raise ValueError(f"WAV 파일이 없습니다: {path}")
            actual = duration(path)
            if valid_time and abs(actual - length) > 0.05 + 0.000001:
                bad(f"{label}: WAV 길이 {actual:.3f}초와 marks 길이 {length:.3f}초가 다릅니다")
        except (KeyError, TypeError, ValueError, OSError, subprocess.CalledProcessError) as error:
            bad(f"{label}: 음성을 확인할 수 없습니다: {error}")
    print(f"동기 검증: 문제 {len(problems)}건")
    return len(problems)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=HERE / "out", help="녹화 결과 디렉터리")
    args = parser.parse_args()
    try:
        return 1 if check_sync(args.out.resolve()) else 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"동기 검증 실패: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

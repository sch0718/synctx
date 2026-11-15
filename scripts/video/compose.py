#!/usr/bin/env python3
"""녹화 프레임과 문장 음성을 합쳐 내레이션 데모와 자막을 만든다."""

import argparse
import json
import math
import os
import subprocess
import sys
from array import array
from pathlib import Path

from check_sync import HERE, check_sync, media_path, number, read_json

RATE = 48000


def run(arguments, **kwargs):
    return subprocess.run(arguments, check=True, **kwargs)


def stamp(seconds):
    millis = max(0, round(seconds * 1000))
    hours, millis = divmod(millis, 3600000)
    minutes, millis = divmod(millis, 60000)
    seconds, millis = divmod(millis, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{millis:03}"


def concat_path(path):
    # ffconcat의 작은따옴표를 닫고 이스케이프한 다음 다시 연다.
    value = str(path)
    if "\n" in value or "\r" in value:
        raise ValueError("프레임 경로에는 줄바꿈을 넣을 수 없습니다")
    return "'" + value.replace("'", "'\\''") + "'"


def compose(out):
    if check_sync(out):
        raise ValueError("동기 검증 문제를 해결한 뒤 합성해 주세요")
    finish = Path.home() / ".claude/skills/narrated-video/scripts/finish.sh"
    if not finish.is_file():
        raise ValueError(f"마무리 스크립트가 없습니다: {finish}")
    marks = read_json(out / "marks.json")
    voice = {(line["scene"], line["i"]): line for line in read_json(out / "voice/lines.json")}
    end = marks["end"]
    frames = marks["frames"]
    if not frames:
        raise ValueError("녹화 프레임이 없습니다")
    previous = -math.inf
    entries = []
    for frame in frames:
        at = frame.get("t")
        if not number(at) or at <= previous:
            raise ValueError("프레임 시각은 유한한 값으로 엄격히 증가해야 합니다")
        previous = at
        path = media_path(frame["file"], out).resolve()
        if not path.is_file():
            raise ValueError(f"프레임 파일이 없습니다: {path}")
        if at < end:
            entries.append((max(0, at), path))
    if not entries:
        raise ValueError("녹화 종료 전 프레임이 없습니다")
    # 녹화 시작 직전 프레임이 여러 개면 마지막 것만 첫 화면으로 쓴다.
    while len(entries) > 1 and entries[1][0] == 0:
        entries.pop(0)
    entries[0] = (0, entries[0][1])
    build = out / "build"
    build.mkdir(parents=True, exist_ok=True)
    listing = ["ffconcat version 1.0"]
    for i, (at, path) in enumerate(entries):
        following = entries[i + 1][0] if i + 1 < len(entries) else end
        listing.extend([f"file {concat_path(path)}", f"duration {following - at:.9f}"])
    listing.append(f"file {concat_path(entries[-1][1])}")
    concat = build / "frames.ffconcat"
    concat.write_text("\n".join(listing) + "\n", encoding="utf-8")
    print(f"영상 인코딩: 프레임 {len(entries)}개, {end:.1f}초", flush=True)
    run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(concat),
         "-vf", "scale=1920:1080:flags=lanczos,setsar=1,fps=30,format=yuv420p",
         "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-t", f"{end:.6f}",
         "-movflags", "+faststart", "-an", str(build / "seg.mp4")])

    # 표본 시각에 음성을 더한다. 디코딩 후 길이가 약간 달라도 겹친 표본을 보존한다.
    mix = array("i", [0]) * math.ceil(end * RATE)
    subtitles = []
    for line in marks["lines"]:
        item = voice[(line["scene"], line["i"])]
        result = run(["ffmpeg", "-v", "error", "-i", str(media_path(item["file"], out / "voice")),
                      "-f", "s16le", "-ac", "1", "-ar", str(RATE), "-"], capture_output=True)
        samples = array("h")
        samples.frombytes(result.stdout)
        if sys.byteorder != "little":
            samples.byteswap()
        offset = round(line["t"] * RATE)
        for i in range(min(len(samples), len(mix) - offset)):
            mix[offset + i] += samples[i]
        subtitles.append((line["t"], min(end, line["t"] + len(samples) / RATE), item["t"]))
    pcm = array("h", (max(-32768, min(32767, sample)) for sample in mix))
    if sys.byteorder != "little":
        pcm.byteswap()
    raw = build / "mix.raw"
    try:
        with raw.open("wb") as stream:
            pcm.tofile(stream)
        print("음성 정규화: -16 LUFS, 48kHz", flush=True)
        run(["ffmpeg", "-v", "error", "-y", "-f", "s16le", "-ar", str(RATE), "-ac", "1",
             "-i", str(raw), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", str(RATE),
             "-ac", "2", "-t", f"{end:.6f}", str(out / "audio.wav")])
    finally:
        raw.unlink(missing_ok=True)
    blocks = []
    for i, (start, stop, text) in enumerate(subtitles):
        # 한 문장을 한 줄로 유지하고 다음 문장과 자막 표시가 겹치지 않게 한다.
        next_start = subtitles[i + 1][0] if i + 1 < len(subtitles) else end
        stop = min(stop + 0.25, next_start, end)
        blocks.append(f"{i + 1}\n{stamp(start)} --> {stamp(stop)}\n{text}\n")
    (out / "synctx-demo.srt").write_text("\n".join(blocks), encoding="utf-8")
    chapters = [{"t": max(0, scene["t"]), "title": scene["title"]} for scene in marks["scenes"]]
    chapters[0]["t"] = 0
    (build / "chapters.json").write_text(json.dumps(chapters, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for chapter in chapters:
        minutes, seconds = divmod(int(chapter["t"]), 60)
        print(f"{minutes}:{seconds:02} {chapter['title']}")
    print(f"최종 영상 합성: 자막 {len(subtitles)}개", flush=True)
    run(["sh", str(finish), str(out / "synctx-demo.mp4"), str(out / "audio.wav"), str(build / "seg.mp4")],
        env={**os.environ, "PHONE": "0"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=HERE / "out", help="녹화 결과 디렉터리")
    args = parser.parse_args()
    try:
        compose(args.out.resolve())
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as error:
        print(f"합성 실패: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

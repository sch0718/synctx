# 내레이션 사용 데모 만들기

가상의 도서 대여 서비스 문서로 실제 Viewer를 조작해, 한국어 설명이 있는 약 4분짜리 영상 하나를 만듭니다. 장면과 대사는 `scenes.json`, 화면 동작과 대사 표는 `script.md`에서 관리합니다.

## 준비

Node.js 22, Python 3, 시스템 Google Chrome, `uv`를 설치하고 프로젝트 루트에서 준비합니다.

```bash
uv sync --all-packages --group browser
npm install -g @marp-team/marp-cli @mermaid-js/mermaid-cli
# macOS에서 시스템 FFmpeg·ffprobe 설치
brew install ffmpeg
```

`node`, `python3`, `git`, `ffmpeg`, `ffprobe`, `marp`, `mmdc`가 PATH에 있어야 합니다. FFmpeg는 `libx264`와 `loudnorm`을 지원해야 합니다. 녹화는 시스템 Chrome의 CDP 스크린캐스트를 쓰므로 Playwright 브라우저·녹화용 FFmpeg 설치는 필요 없습니다.

다음 공용 스크립트도 준비해야 합니다.

- `~/.claude/skills/narrated-video/scripts/tts.mjs`: 음성 생성
- `~/.claude/skills/narrated-video/scripts/finish.sh`: 최종 영상 합성

Gemini 키는 `~/.config/belorn/voice.env`에 설정합니다. 키 값이나 파일 내용을 로그·공유 자료에 출력하지 마세요. 기본은 Gemini `Sulafat`로 4묶음을 생성하며, Gemini 한도로 거절되면 `--provider edge`로 전체 음성을 다시 만듭니다. Edge는 `ko-KR-SunHiNeural`을 사용합니다. 음성 생성은 네트워크에 접근하며 공용 TTS 스크립트의 추가 실행 도구(Edge의 `uvx` 등)도 필요할 수 있습니다. `UV_OFFLINE=1`은 녹화 단계에만 적용합니다.

## 실행

```bash
# 단계별 실행
scripts/video/make.sh voice
scripts/video/make.sh record
scripts/video/make.sh compose

# 전체 실행(인자를 생략해도 동일)
scripts/video/make.sh all

# Edge로 전체 음성 다시 생성
scripts/video/make.sh voice --provider edge

# 음성 묶음 하나만 다시 생성: 0부터 시작하는 번호 0~3
scripts/video/make.sh voice --only 0

# 결과 위치와 녹화용 작업 폴더 지정
scripts/video/make.sh all --out /tmp/synctx-demo --work /tmp/synctx-video-work
```

음성 원본은 대본·목소리 설정이 같으면 `voice/raw/`의 캐시를 다시 씁니다. 같은 대본으로 새로 받으려면 그 묶음의 `voice/raw/g<번호>-*.wav`를 지운 뒤 실행하세요.

`--out`은 전체 출력 폴더이며 기본값은 `scripts/video/out`입니다. 음성은 그 아래 `voice/`에 저장합니다. `--provider`와 `--only`는 음성 단계에만 적용합니다. `record`는 `voice/lines.json`이 없으면 멈춥니다. 음성을 바꾸면 녹화와 합성도 다시 실행하세요.

`record`만 시연 저장소·설정·캐시와 Viewer 서버를 만듭니다. `--work` 기본값은 `${TMPDIR:-/tmp}/synctx-video`이며 프로젝트 밖의 별도 폴더여야 합니다. 없거나 비었거나 이 도구의 `.synctx-video-work` 표식이 있는 폴더만 사용합니다. 매번 시연 저장소와 캐시를 새로 만들므로 같은 작업 폴더로 동시에 실행하지 마세요.

서버는 `scripts/local/run-viewer.sh`로 18090 포트에 띄웁니다. 사용 중인 포트는 거절하며, 최대 60초 동안 `/view/readyz`의 HTTP 200과 시연 문서 응답을 확인한 뒤 녹화합니다. 종료·오류·중단 때 서버를 정리하며 실패하면 작업 폴더의 `viewer.log` 끝부분을 출력합니다. 음성 생성과 합성은 서버를 띄우지 않습니다.

## 확인과 업로드

녹화 후 동기화를 확인하고 합성합니다. 다른 출력 폴더를 썼다면 아래 경로도 맞추세요.

```bash
python3 scripts/video/check_sync.py --out scripts/video/out
scripts/video/make.sh compose
ffmpeg -ss 12 -i scripts/video/out/synctx-demo.mp4 -frames:v 1 /tmp/synctx-demo-12초.png
ffprobe -v error -show_entries stream=codec_name,width,height,r_frame_rate,sample_rate:format=duration \
  -of json scripts/video/out/synctx-demo.mp4
```

결과는 `synctx-demo.mp4`, 별도 한국어 자막 `synctx-demo.srt`, 챕터 시각 `build/chapters.json`입니다. 음성은 `voice/`, 녹화 시각은 `marks.json`, 원본 화면은 `frames/`, 내려받은 파일은 `downloads/`에 남습니다. 출력 폴더는 커밋하지 않습니다.

`check_sync.py` 문제 0건, 1920×1080·30fps·H.264·AAC 48kHz, 길이 3~5분을 확인하세요. 장면마다 프레임을 추출해 커서·글자·빈 화면·불필요한 정보 노출을 확인하고 완성 영상을 재생해 음성을 들어 보세요. 듣지 않았다면 검수 기록에도 그렇게 적습니다.

유튜브는 일부 공개, 카테고리 28로 올리고 SRT를 Studio에서 수동 업로드합니다. `youtube.md` 설명에는 챕터와 녹화 환경, `voice/lines.json`에 기록된 실제 `provider`·`voice`를 적습니다. Edge로 대체했으면 Edge로 표시하세요. 이 실행기는 업로드를 수행하지 않습니다.

정적 검사는 실행·시청 검수를 대신하지 않습니다.

```bash
python3 -m py_compile scripts/video/record.py scripts/video/compose.py scripts/video/check_sync.py
bash -n scripts/video/make.sh
node --check scripts/video/voice.mjs
uvx ruff check scripts/video
```

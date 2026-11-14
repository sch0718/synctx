# synctx 내레이션 사용 데모 영상 계획 (2026-11-14)

메뉴별 무음 자막 영상 7개를 걷어 내고, 음성으로 설명하는 4분 안팎의 데모 영상 하나로 바꾼다. 이미 있는 장치로 안 되는 이유: 메뉴별 영상은 자막만 있고 사람이 쓰는 흐름이 끊겨, 사용자가 원한 「설명을 들으며 한 바퀴 보는 영상」이 아니다.

전제: 보는 사람은 링크를 받은 소수(유튜브 일부 공개). 비용 0원(Gemini TTS 무료 등급, 하루 10번). 다시 만들 일은 드물다.

## 결과물

- `scripts/video/out/synctx-demo.mp4`: 1920x1080 30fps, H.264 + AAC 48kHz. 커밋하지 않는다(`.gitignore`의 `scripts/video/out/`).
- `scripts/video/out/synctx-demo.srt`: 한국어 자막, 한 문장 한 줄.
- `scripts/video/youtube.md`(커밋): 제목 「synctx 사용 데모: …」, 설명(챕터 시각, 「AI 음성(<실제 공급자>)」, 녹화 환경). 공급자는 `out/voice/lines.json`의 provider·voice 값을 따른다(Edge로 대체하면 Edge로 적는다), 카테고리 28, 일부 공개, 자막은 Studio에서 수동 업로드.

## 파일

| 파일 | 할 일 |
|---|---|
| `scripts/video/scenes.json` | 장면 8개(key·title·lines), 읽기 치환 `speak`, TTS 묶음 `groups`(4묶음 = 요청 4번). 이미 썼다 |
| `scripts/video/script.md` | 장면마다 「화면 동작 \| 말할 문장」 표. 이미 썼다. 문장은 scenes.json과 같게 둔다 |
| `scripts/video/voice.mjs` | 새로. repass `rec/build-voice.mjs`와 같은 방식: 묶음마다 `tts.mjs`의 `speak` 한 번 → 무음 검출로 문장 경계를 동적 계획으로 골라 `out/voice/<key>-<i>.wav`와 `out/voice/lines.json`(scene, i, t, file, dur, provider, voice). 기본 Gemini `Sulafat`(친구에게 보여 주는 존댓말 말투 지시), `--provider edge`면 `ko-KR-SunHiNeural`. 키는 `~/.config/belorn/voice.env`에서 읽고 값은 출력하지 않는다. `speak` 치환은 TTS에 보낼 글에만 적용 |
| `scripts/video/record.py` | 바꿈. 메뉴별 녹화를 지우고 한 컨텍스트에서 장면 8개를 이어서 녹화. 아래 「녹화」 |
| `scripts/video/compose.py` | 새로. `out/marks.json` + 프레임 + 문장 wav → `out/build/seg.mp4`(프레임마다 다음 프레임 시각까지, 30fps), `out/audio.wav`(문장을 녹화 시각에 놓고 loudnorm -16 LUFS), `out/synctx-demo.srt`, `out/build/chapters.json`. 이어서 `PHONE=0 sh ~/.claude/skills/narrated-video/scripts/finish.sh out/synctx-demo.mp4 out/audio.wav out/build/seg.mp4` |
| `scripts/video/check_sync.py` | 새로. 장면마다 화면 준비 시각(ready)과 첫 문장 시각 차가 1초를 넘으면, 문장끼리 겹치면, ready가 없으면, marks의 장면·문장이 scenes.json과 개수·순서가 다르면, 문장 wav가 없거나 길이가 marks의 dur와 0.05초 넘게 다르면 문제로 세고 종료 코드 1 |
| `scripts/video/make.sh` | 바꿈. 단계 인자 `voice` / `record` / `compose` / `all`(기본). `record`만 시연 저장소·설정·Viewer 서버를 띄운다(지금 코드 그대로). `--menu` 제거. `record`는 `out/voice/lines.json`이 없으면 멈춘다 |
| `scripts/video/README.md` | 바꿈. 준비물, 단계별 명령, 결과 확인(프레임 뽑기, check_sync), 업로드 안내 |
| `scripts/video/demo_repo.py`, `demo-docs/` | 그대로 쓴다 |

## 녹화 (`record.py`)

- Playwright sync, `chromium.launch(channel="chrome", headless=True)`. 컨텍스트 `viewport 1280x720`, `device_scale_factor=1.5`, `locale="ko-KR"`, `timezone_id="Asia/Seoul"`, `accept_downloads=True`, `bypass_csp=True`(Viewer CSP가 `wait_for_function`을 막는다).
- 화면은 Playwright 영상 대신 CDP `Page.startScreencast`(jpeg 88, 최대 1920x1080)로 `out/frames/NNNNNN.jpg`와 프레임 시각(metadata.timestamp)을 모은다. 미리보기 새 탭이 뜨면 원래 탭 스크린캐스트를 멈추고 새 탭에서 시작한다(슬라이드·마무리는 새 탭에서 끝난다).
- 겹침 층: 지금의 `popover="manual"` 커서·클릭 표시를 유지한다(대화 상자 위에도 보이게). 자막은 화면에 그리지 않는다(srt로 따로).
- 사람처럼: 커서는 살짝 휘는 궤적과 가감속으로 움직이고, 읽는 동안 조금씩 떠돌고(`wander`), 고르기 전에 다른 곳에서 머뭇거리고, 글자는 한 자씩 80~170ms 간격으로 친다. 난수는 씨앗을 고정한다.
- 시각: 모든 시각(프레임, 문장, ready, 장면 시작, end)은 녹화 시작 시각 `t0`(첫 스크린캐스트를 켠 직후의 `time.time()`)부터의 초로 적는다. 프레임은 CDP `metadata.timestamp`(에포크 초) − `t0`. 새 탭으로 바꿔도 `t0`는 그대로 두고, 바꾼 뒤 첫 프레임 시각이 마지막 원래 탭 프레임보다 뒤인지 확인한다.
- 박자: `say(i)`는 앞 문장이 끝나고 0.35초 쉰 뒤 그 문장 시각을 marks에 남기고 돌아온다. 화면 동작은 그 뒤에 이어진다. 장면 첫 화면이 다 뜨면 `ready()`. 장면 끝은 마지막 문장이 끝나고 0.7초 뒤.
- 셀렉터는 지금 `record.py`의 메뉴별 시나리오(역할·글자 기반)를 이어서 쓴다. 장면 사이 이동은 클릭으로 한다(검색 결과로 문서 열기, 브랜치 main으로 되돌리고 트리에서 회의 문서 열기, 「회의 모드 나가기」, 트리에서 발표 문서 열기).
- 테마는 선택 목록을 펼치지 않고 `select_option`으로만 바꾼다(목록에 시연과 관계없는 이름이 있다). 문서 장면 끝에 GitHub 밝게로 되돌린다.
- 결과: `out/marks.json`(`end`, `scenes[{scene,title,t,ready}]`, `lines[{scene,i,t,dur}]`, `frames[{file,t}]`). 내려받은 파일은 `out/downloads/`.

## 순서

1. 대본·계획 검토(codex-review `--doc` 1회) 뒤 반영.
2. Codex로 `voice.mjs`·`record.py`·`compose.py`·`check_sync.py`·`make.sh`·README 구현(녹화는 돌리지 않고 정적 검사까지).
3. `make.sh voice`(Gemini 4번. 한도로 거절되면 Edge로 전부 다시).
4. `make.sh record` → `check_sync.py` → 어긋나면 장면 동작·쉼을 고쳐 다시 녹화. 이 반복은 이 세션이 직접 한다.
5. `make.sh compose` → 프레임 뽑아 보기.
6. Opus 구현 검토(최대 2회, Warning 이상만 수정). 수정이 생기면 영향받는 가장 앞 단계(voice·record·compose)부터 다시 돌리고, check_sync·ffprobe·프레임 확인을 다시 통과한 결과만 올린다.
7. `youtube.md` 작성, 커밋·push, PR #85 제목·본문 교체, 업로드(`--check` → `--privacy unlisted --category 28`). 성공하면 README에 한 줄을 넣고 검사·병합, 거절되면 병합하지 않고 보고.

## 검증

- `uvx ruff check scripts/video`, `bash -n scripts/video/make.sh`, `node --check scripts/video/voice.mjs`.
- `check_sync.py` 문제 0건.
- `ffprobe`로 1920x1080, 30fps, h264, aac, 길이 3~5분.
- 장면마다 프레임을 뽑아 Read로 본다: 회사 이름·사내 도메인 없음, 커서 보임, 빈 화면 없음.
- `scripts/test-all.sh` 통과(녹화 스크립트는 시험 대상이 아니지만 저장소 전체가 깨지지 않았는지).
- 음성은 사람이 들어 보기 전에는 「들어 보지 못함」으로 보고한다.

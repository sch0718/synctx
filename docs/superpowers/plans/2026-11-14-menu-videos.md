# 메뉴별 사용 영상 (2026-11-14)

README 「메뉴별 도움말」의 메뉴 7개(browse·search·document·meeting·present·evidence·slides)마다 **실제 Viewer 화면**을 녹화한 30~60초 영상을 만들고, 유튜브에 일부 공개로 올려 README 표에 링크한다.

- 전제: 운영자 1명, 비용 없음. 영상은 외부(유튜브)에 나가므로 화면에 회사 이름·사내 문서·사내 도메인이 나오면 안 된다. 그래서 시연용 중립 문서 저장소를 따로 만든다.
- 이미 있는 것으로 안 되는 이유: `scripts/build_motion.py`의 도움말 그림은 가짜 화면 애니메이션이라 실제 조작 모습을 보여 주지 못한다.
- 범위 밖: 목소리, `scripts/motion/` 변경, `docs/help/` 직접 수정, 정적 Viewer 화면, 슬라이드 `출판…`(출판 저장소를 설정한 배포에만 있는 기능이라 시연 환경에 없다).

## 파일

| 파일 | 내용 |
|---|---|
| `scripts/video/demo-docs/` | 시연 문서 원본(아래 「시연 문서」) |
| `scripts/video/demo_repo.py` | 원본을 지정한 폴더에 복사해 git 저장소로 만든다. `main` 커밋 뒤 `draft` 브랜치에 한 문서를 고친 커밋을 하나 더 둔다. 회의 문서의 `starts_at`은 실행 시각 10분 전으로 채워 `진행 중`이 보이게 한다 |
| `scripts/video/record.py` | Playwright로 메뉴별 조작을 재생하며 녹화한다. `--menu <이름>`(여러 번 가능, 없으면 7개 전부), `--base-url`, `--out` |
| `scripts/video/make.sh` | 한 번에 다시 만드는 명령: 아래 준비물 검사 → 시연 저장소 만들기 → 설정 파일 쓰기 → `scripts/local/run-viewer.sh --config <설정> --cache-dir <work>/cache --port 18090`을 백그라운드로 띄우기 → 제한 시간·서버 조기 종료 검사와 함께 `/view/readyz`의 HTTP 200 기다리기 → 시연 문서 열기 확인 → `record.py` → 서버 끄기(trap) |
| `scripts/video/README.md` | 아래 준비물·설치 절차와 다시 만드는 명령, 결과 위치 |
| `.gitignore` | `scripts/video/out/` 추가(영상 파일은 커밋하지 않는다) |
| `README.md` | 「메뉴별 도움말」 표에 「영상」 열, 표 위에 일부 공개 안내 한 줄 |

임시 저장소·캐시·설정은 `--work <폴더>`(기본 `${TMPDIR}/synctx-video`)에 두고 저장소 안에는 만들지 않는다. 설정은 저장소 하나(`id: demo`, `name: "예시 문서"`, `url: <로컬 경로>`, `auth: {type: none}`, `collab: {enabled: true}`), `authz.enabled: false`, `viewer.asset_source: bundled`, `viewer.repo_cache_path: <work>/repos`로 생성한다. 여기서 `<work>`는 `--work` 폴더의 절대 경로다. `--config`를 쓰면 `build_local_config`의 로컬 경로 보정을 건너뛰므로 저장소 캐시 경로는 설정에 직접 넣고, 실행 시 `--cache-dir <work>/cache`도 지정해 실행기가 만드는 캐시 디렉터리 역시 작업 폴더 아래에 둔다. 2026-11-14 확인: `run-viewer.sh --config`에 로컬 경로 저장소를 주면 그대로 열린다.

녹화 전 준비 확인은 `services/viewer/tests/browser/conftest.py`의 서버 시작 절차처럼 제한 시간(60초) 안에 `/view/readyz`가 200이 될 때까지 기다린다. 그동안 서버 프로세스가 끝나거나 시간을 넘기면 오류로 끝내고 trap으로 정리한다. `/view/`는 초기 복제 중에도 200을 주므로 준비 판정에 쓰지 않는다. 준비 응답 뒤에는 시연 문서마다 `/view/demo/<경로>`가 200인지 확인하고, 하나라도 실패하면 녹화하지 않는다.

생성 설정에는 `runtime.public_base_url: http://127.0.0.1:18090`, `viewer.cdn_base_url: /cdn`, `viewer.marp_service_url: http://127.0.0.1:18090/marp`, `marp.worktree_path: <work>/marp`도 넣는다(`--config`를 쓰면 `build_local_config`가 채우던 값이 기본값으로 남기 때문).

## 녹화·변환 준비물

- 시스템 Chrome과 `uv sync --group browser`로 설치한 Playwright를 쓴다.
- 녹화용 Playwright 관리 FFmpeg는 별도로 `uv run --group browser playwright install ffmpeg`로 준비한다. Chrome을 사용하므로 Playwright 브라우저는 내려받지 않는다. 이 FFmpeg는 아래 변환용 시스템 FFmpeg와 별개다.
- 변환·검증용 시스템 `ffmpeg`와 `ffprobe`를 설치하고 PATH에 둔다(macOS 예: `brew install ffmpeg`). `ffmpeg`는 계획의 H.264 변환에 필요한 `libx264` 인코더를 지원해야 한다.
- Marp용 `marp`·`mmdc`도 PATH에 있어야 슬라이드 메뉴가 된다.

구현할 `make.sh`는 서버를 띄우기 전에 시스템 Chrome, Playwright 관리 FFmpeg 실행 파일, PATH의 `ffmpeg`·`ffprobe`·`marp`·`mmdc`를 검사한다. 시스템 `ffmpeg -encoders` 결과에 `libx264`가 있는지도 검사한다. 하나라도 없거나 실행할 수 없으면 필요한 설치 절차를 출력하고 실패로 종료한다. 준비물 설치는 위 절차로 미리 수행하고, 녹화 실행 중에는 다운로드하지 않는다.

## 시연 문서

가상의 「사내 도서 대여 서비스」 문서 묶음. 회사·사람 이름은 쓰지 않는다(담당자는 `민지`, `준호` 같은 이름만).

| 경로 | 쓰는 메뉴 | 담는 것 |
|---|---|---|
| `README.md` | browse | 저장소 소개, 폴더 안내 |
| `설계/시스템-구성.md` | browse·document·search | h2 여러 개(목차), 표, Mermaid flowchart, 코드 블록(python·yaml) |
| `설계/대여-규칙.md` | search·evidence | `## 제1조(대여 기간)` 같은 조문 절, 근거로 인용될 대상, `title:` Front Matter |
| `운영/장애-대응.md` | search | 본문 검색어(`대여 기간`, `연체`)가 겹치게 |
| `회의/2026-11-주간-회의.md` | meeting·present·evidence | `synctx.type: meeting`, `synctx.meeting`(id·title·starts_at·duration_minutes·channel·presenters), h2 안건 4개 이상(발표 장), 같은 저장소 상대 링크 `../설계/대여-규칙.md#제1조대여-기간` 같은 근거 링크 2개 이상 |
| `발표/서비스-소개.md` | slides | `marp: true`, `theme: gaia`, `paginate: true`, `---`로 나뉜 4~5장 |

## 녹화 방식

- `services/viewer/tests/browser/conftest.py`처럼 `chromium.launch(channel="chrome", headless=True)`(시스템 Chrome, 브라우저 다운로드 없음; 녹화용 FFmpeg는 위 준비 절차로 설치).
- 컨텍스트: viewport·`record_video_size` 1920×1080, `accept_downloads=True`, `locale="ko-KR"`. 2026-11-14 확인: Playwright 1.64의 녹화는 VP9 25fps이고 14px 글자가 또렷하다.
- `context.add_init_script`로 모든 페이지에 겹침 층을 넣는다.
  - 가짜 커서(화살표 SVG)와 클릭할 때 퍼지는 원. 커서 위치는 `mousemove`를 따라간다.
  - 화면 아래 가운데 자막 막대(반투명 검정 바탕, 흰 글자 36px 안팎, 한 줄).
  - 둘 다 `popover="manual"` 요소에 넣어 top layer에 둔다. Viewer가 `<dialog>.showModal()`(검색·단축키 창)을 쓰므로, 단계마다 `hidePopover()`→`showPopover()`로 다시 올려 모달 위에 보이게 한다. `pointer-events: none`.
- 도우미: `caption(text, hold_ms)`, `move_to(locator)`(현재 위치에서 `mouse.move(..., steps=25)`로 움직임), `click(locator)`, `type_slow(text)`(한 글자 80ms 안팎), `press(key)`(자막에 누른 키를 함께 보인다). 요소는 역할·글자(`get_by_role`, `get_by_text`)로 찾는다.
- 슬라이드 미리보기는 이동할 URL이 없다(`viewer.js`의 `marpPreview`가 `window.open('', '_blank')`로 빈 창을 연 뒤 POST 응답 HTML을 그 창에 `document.write`한다). 그래서 제품 동작은 건드리지 않고 실제 팝업을 그대로 녹화해 두 영상을 잇는다.
  - 단추를 누를 때 `context.expect_page()`로 팝업을 받고, 원래 페이지 녹화 기준의 누른 시각(`t_click`)을 적는다. 팝업은 `wait_for_function`으로 슬라이드가 그려질 때까지 기다린 시각(`t_ready`)부터 쓴다.
  - `document.open`이 겹침 층을 지우므로, 팝업에는 그려진 뒤 `evaluate`로 겹침 층을 다시 넣고 자막에 「새 탭에서 열립니다」라고 쓴다.
  - 장을 넘긴 뒤 `popup.close()`(`t_close`)하고 원래 페이지로 돌아와 `PDF 만들기`를 이어 간다. 원래 페이지는 그동안 그대로 있으므로 따로 복귀할 것이 없다.
  - 인코딩 때 원래 페이지 `[0.5, t_click]`(앞 0.5초 자르기를 여기서 한 번만 한다) → 팝업 `[t_ready, t_close]`(팝업 영상은 팝업이 생긴 시각이 0) → 원래 페이지 `[t_close, 끝]`을 잘라 `ffmpeg concat`으로 한 영상으로 잇는다. 시각은 각 페이지가 생긴 직후 `time.monotonic()`을 기준으로 잰다.
- 페이지가 처음 뜨는 동안의 빈 화면을 줄이려고, 시나리오는 대상 페이지를 연 뒤 0.5초 기다리고 시작한다. 인코딩할 때 앞 0.5초를 자른다.
- 끝나면 webm을 `ffmpeg -c:v libx264 -crf 18 -preset slow -pix_fmt yuv420p -r 30 -movflags +faststart`로 `out/<메뉴>.mp4`로 바꾸고, 길이가 30~60초를 벗어나면 경고를 찍는다. 폰용 축소는 만들지 않는다(유튜브가 해상도별로 만든다).

## 메뉴별 시나리오 (자막은 단계마다 한 줄)

| 메뉴 | 단계 |
|---|---|
| browse | `/view/` 저장소 목록 → 「예시 문서」 카드 → 트리에서 `설계` 펼치고 `시스템-구성.md` → 본문 스크롤(Mermaid·코드) → 오른쪽 `이 문서` 목차에서 절 누르기 → `t`로 목차로 이동(1100px 이상에서는 오른쪽 목차 첫 항목으로 초점이 간다) |
| search | 문서 화면에서 `⌘K`(Meta+K) → `대여` 입력 → `파일`·`본문` 결과 → ↓↓ Enter로 열기 → `/`로 다시 열고 `연체` → 전체 결과 보기 → `모든 저장소` 상자 보여 주기 |
| document | `시스템-구성.md` → 상단 `테마`에서 두 가지 바꿔 보기 → `내려받기` 열어 `원본`·`HTML`·`PDF` 보이기(받기는 `HTML` 하나) → `브랜치`에서 `draft`로 바꿔 달라진 문단 보이기 → `?` 단축키 창 → Esc |
| meeting | 회의 문서 → 머리 카드(진행 중) → `회의 모드로 열기` → 안내 창 닫기 → `+ 메모`로 일반 메모 → 글 선택 후 `+ 선택 영역에 메모`로 `결정` → `액션`(담당자·기한) → `회의 정리` → `.md로 내보내기` |
| present | 회의 문서 → `발표하기` → → 두 번, Space → `메모 보기(N)` → 첫 Esc로 메모 패널 닫기 → 두 번째 Esc로 발표 끝내기 → 문서 화면 복귀 확인 |
| evidence | 회의 문서 → 근거 링크에 마우스 올려 말풍선 → Esc → 다른 근거 링크 누르기 → 문서 끝 `근거 모음` → `인용한 곳` → `원문 열기` |
| slides | `발표/서비스-소개.md` → `슬라이드 미리보기` → 장 넘기기 → 문서로 돌아와 `PDF 만들기` → `PDF 만드는 중…` → 받기 완료 |

각 영상은 첫 자막에 「synctx 사용법 — <메뉴 이름>」, 마지막 자막에 도움말 문서 경로(`docs/help/<메뉴>.md`)를 보인다. 실제 단추·글자 이름이 위 표와 다르면 Viewer 화면(`services/viewer/src/static/*`, `viewer_ui.py`)의 실제 이름을 따른다.

## 순서

1. Codex: 위 파일들을 만든다(녹화 실행은 하지 않는다. 샌드박스라 Chrome·서버가 안 될 수 있다).
2. 이 세션: `make.sh`로 7개 녹화 → 메뉴마다 프레임 3~4장을 PNG로 뽑아 직접 본다(회사 이름·글자 넘침·빈 화면·자막 가림). 고칠 것은 `record.py`를 고쳐 그 메뉴만 다시 녹화.
3. 구현 검토(하위 에이전트, 최대 2회) → 지적 처리.
4. 유튜브 업로드: 저장소 밖 narrated-video 스킬의 `~/.claude/skills/narrated-video/scripts/youtube_upload.py`를 `uv run --with google-api-python-client --with google-auth-oauthlib`로 부른다(준비·승인 파일은 그 스크립트 머리말). `x --title x --check`로 `AUTH_OK` 확인(종료 코드 3이면 사람이 `--auth`로 다시 승인) → 메뉴마다 하나씩 백그라운드로 `--privacy unlisted --category 28`. 제목 「synctx 사용법 — <메뉴 이름>」, 설명은 도움말 요약 + 「시연용 예시 문서로 녹화했습니다」(5,000바이트 이하, `<` `>` 없음). 출력 줄의 `privacy=unlisted`와 `https://youtu.be/<id>`를 7개 모두 확인해 README 링크로 쓴다.
5. README 표에 링크 → 검사 → PR → 병합.

## 검증

- 단위 시험은 두지 않는다(녹화 스크립트는 사람이 결과 영상을 보고 확인하는 도구이고, 동작을 지킬 제품 코드가 아니다). 대신 `uv run ruff check scripts/video`(저장소의 린트 설정이 있으면)와 `bash -n scripts/video/make.sh`.
- 영상: `ffprobe`로 1920×1080·길이 30~60초 확인, 프레임 PNG 확인.
- 저장소: `scripts/test-all.sh`(바뀐 것이 스크립트·문서뿐이라 기존 시험이 그대로 통과해야 한다).

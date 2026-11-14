# 메뉴별 사용 영상 만들기

실제 Viewer에서 일곱 메뉴를 조작해 1920×1080 영상을 만듭니다. 가상의 도서 대여 서비스 문서만 사용하며, 음성 없이 커서·클릭 표시와 한국어 자막을 넣습니다. 루트 README와 도움말 문서는 바꾸지 않습니다.

## 준비물 설치

프로젝트 루트에서 다음 준비를 한 번 수행하세요.

```bash
uv sync --all-packages --group browser
uv run --group browser playwright install ffmpeg
```

시스템 Google Chrome을 설치하세요. 녹화는 `chromium.launch(channel="chrome", headless=True)`를 사용하므로 Playwright 브라우저 설치는 필요 없습니다. 위 명령은 녹화용 Playwright 관리 FFmpeg만 설치합니다.

변환용 시스템 FFmpeg와 ffprobe도 별도로 필요합니다. macOS에서는 다음처럼 설치합니다. 다른 운영체제에서는 해당 패키지 관리자를 사용하세요.

```bash
brew install ffmpeg
npm install -g @marp-team/marp-cli @mermaid-js/mermaid-cli
```

`git`, `uv`, `ffmpeg`, `ffprobe`, `marp`, `mmdc`가 PATH에 있어야 하며, 실행기가 처음에 이것만 확인합니다. 시스템 FFmpeg는 `libx264` 인코더를 지원해야 합니다(Homebrew 판은 기본 포함). Chrome이나 Playwright 녹화용 FFmpeg가 없으면 녹화를 시작할 때 오류가 납니다.

실행기는 `UV_OFFLINE=1`로 동작합니다. 녹화 도중에는 의존성·브라우저·FFmpeg를 내려받지 않으므로 필요한 패키지를 먼저 설치하세요. 의존성 설치 명령 자체가 실패하면 로그를 확인한 뒤 위 준비 절차를 다시 수행하세요.

## 다시 만들기

```bash
# 모든 메뉴
scripts/video/make.sh

# 지정한 메뉴만 다시 녹화
scripts/video/make.sh --menu meeting --menu slides

# 작업 폴더와 결과 폴더 지정
scripts/video/make.sh --work /tmp/도서-시연 --out scripts/video/out
```

| 메뉴 값 | 내용 |
|---|---|
| browse | 저장소·파일 트리·목차 |
| search | 파일 이름과 본문 검색 |
| document | 테마·내려받기·브랜치·단축키 |
| meeting | 일반 메모·선택 영역의 결정·담당자와 기한이 있는 액션·내보내기 |
| present | 안건별 발표·메모 패널·발표 끝내기 |
| evidence | 근거 말풍선·근거 모음·인용한 곳·원문 |
| slides | PDF 만들기·새 탭 미리보기·장 넘기기 |

`--work` 기본값은 `${TMPDIR:-/tmp}/synctx-video`입니다. 프로젝트 밖의 별도 경로만 사용할 수 있습니다. 그 아래에 `demo/`, `synctx.yaml`, `repos/`, `cache/`, `marp/`, `viewer.log`를 둡니다. 작업 폴더는 없거나 비어 있거나 이 도구가 남긴 표식(`.synctx-video-work`)이 있을 때만 씁니다. 실행할 때마다 `demo/`·`repos/`·`cache/`·`marp/`를 지우고 새로 만듭니다(시연 저장소 이력이 매번 새로 생겨 이전 캐시와 맞지 않기 때문). 같은 작업 폴더로 동시에 실행하지 마세요.

시연 저장소는 기본 `main` 브랜치와 문단 하나를 바꾼 `draft` 브랜치를 갖습니다. 커밋은 임시 시연 저장소에만 만듭니다. 회의 시작 시각은 생성 시각의 10분 전, 회의 길이는 120분으로 채웁니다. 오래 지난 서버를 재사용할 때는 저장소와 서버부터 다시 만들어야 진행 중 상태가 나옵니다.

실행기는 18090 포트 사용 여부와 준비물을 검사한 뒤 `scripts/local/run-viewer.sh`로 서버를 시작합니다. 최대 60초 동안 `/view/readyz`의 HTTP 200을 기다리고, 시연 문서 여섯 개도 모두 HTTP 200인지 확인합니다. 준비 중 서버가 끝나거나 문서가 열리지 않으면 녹화하지 않습니다. 종료·오류·중단 시 서버를 정리하며, 실패하면 `viewer.log` 끝부분을 출력합니다.

이미 준비된 시연 서버가 있다면 녹화만 실행할 수도 있습니다.

```bash
uv run --frozen --no-sync --all-packages --group browser python scripts/video/record.py \
  --base-url http://127.0.0.1:18090 --menu browse --out scripts/video/out
```

직접 녹화할 때는 준비물 검사와 서버 준비 확인을 직접 마쳐야 합니다. 저장소 식별자는 `demo`, 표시 이름은 `예시 문서`여야 합니다.

## 결과와 검수

기본 결과는 `scripts/video/out/<메뉴>.mp4`입니다. 이 폴더는 Git에서 제외합니다. H.264·CRF 18·30fps·yuv420p로 변환하고 빠른 재생 시작 정보를 넣습니다. 원본은 `out/raw/<메뉴>/`, 시연 중 내려받은 파일은 `out/downloads/<메뉴>/`에 남깁니다. 슬라이드 탭의 편집 시각은 원본 폴더의 `timing.json`에 기록합니다.

메뉴마다 새 컨텍스트를 사용해 메모와 표시 설정을 분리합니다. 자막은 보통 한 줄에 3초 이상 유지하며 조작 시간을 포함해 30~60초를 목표로 합니다. 실제 문서 로딩과 PDF 변환 속도에 따라 길이는 달라질 수 있습니다. 변환 뒤 ffprobe로 해상도를 확인하고 길이가 범위를 벗어나면 경고합니다.

슬라이드는 실제 새 탭을 녹화합니다. 새 탭을 닫고 원래 탭으로 돌아오면 헤드리스 Chrome의 녹화 크기가 바뀌어 화면 아래가 회색으로 잘리므로, `PDF 만들기`를 먼저 하고 미리보기 탭에서 영상을 끝냅니다. 원래 탭은 미리보기를 누르기 전까지, 새 탭은 슬라이드가 준비된 뒤부터 끝까지 이어 붙입니다. 시작의 0.5초 자르기는 원래 탭에만 한 번 적용합니다.

업로드 전에는 각 영상을 직접 재생하고, 앞·중간·끝에서 프레임을 3~4장씩 확인하세요. 예를 들어 다음 명령으로 한 장을 꺼낼 수 있습니다.

```bash
ffmpeg -ss 12 -i scripts/video/out/browse.mp4 -frames:v 1 /tmp/문서-탐색-12초.png
ffprobe -v error -show_entries stream=width,height,r_frame_rate:format=duration \
  -of json scripts/video/out/browse.mp4
```

자막 가림, 글자 넘침, 빈 화면, 불필요한 정보 노출과 30~60초 길이를 확인하세요. 수정할 때는 `record.py`의 해당 메뉴와 자막 유지 시간을 고치고 그 메뉴만 다시 녹화합니다. 서버·Chrome·녹화·변환·프레임 검수는 실제 실행 환경에서 확인해야 합니다. 이 도구는 업로드나 루트 README의 링크 추가를 수행하지 않습니다.

## 정적 검사

```bash
python -m py_compile scripts/video/demo_repo.py scripts/video/record.py
bash -n scripts/video/make.sh
uvx ruff check scripts/video
```

ruff가 설치되어 있지 않다면 별도로 준비하세요. 정적 검사는 실제 화면 조작과 영상 검수를 대신하지 않습니다.

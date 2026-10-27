# README 서비스 소개 개편과 메뉴별 도움말

## 승인된 범위

2026-10-27 대화에서 승인한 구성이다. 같은 사용자의 landmark(`8b75dfe`·`01a6bde`)와 gazua(`f3f2c5a`)가 README를 바꾼 방향을 synctx에 같은 방식으로 적용한다.

- README는 서비스 소개만 담는다. 순서는 움직이는 소개 그림 → 한 줄 소개 → 짧은 설명 → 「이런 걸 할 수 있어요」 표 → 시작하기 → 간단한 사용법 → 메뉴별 도움말 표 → 데이터는 어디에 있나요 → 릴리스 → 개발자용 링크.
- 개발자용 내용(프로젝트 배경, 아키텍처 다이어그램, 서비스 표, 배포 방식·빠른 시작, 서비스별 설명, 동기화·출판 흐름, 주요 개념, 프로젝트 구조, 기술 스택, 기존 자산, 문서 목록)은 `docs/development.md`로 옮긴다.
- CLAUDE.md의 문서 구조 표와 「3개 문서 일관성」 규칙에서 README 자리를 `docs/development.md`로 바꾼다. 다이어그램 규칙(External/Internal subgraph)도 그 파일에 적용한다.
- 메뉴별 도움말 7개를 `docs/help/`에 둔다: 저장소·문서 찾아가기(`browse`), 문서 검색(`search`), 문서 보기·내려받기(`document`), 회의 모드(`meeting`), 발표 화면(`present`), 근거 미리보기·근거 모음(`evidence`), 슬라이드(`slides`). 동기화·Draw.io·CDN은 README 소개 표에서만 설명한다.
- 열린 PR #61(경량 Viewer)의 README 변경은 이 작업에 넣지 않는다. 이 브랜치는 `origin/main`에서 시작하고, #61과의 충돌은 #61을 병합할 때 푼다.
- Viewer 앱 안의 도움말 화면은 범위 밖이다(두 저장소에서도 별도 기능 커밋이었다).

## 그림 생성기

landmark·gazua의 `scripts/motion/*.mjs` 구조를 Python 표준 라이브러리로 옮긴다. 저장소의 다른 도구와 같이 pytest로 검사하기 위해서다.

- `scripts/motion/kit.py`: 글자 폭 근사·자르기, 칩·카드·단추, 누르는 곳 표시, 애니메이션 CSS(장면마다 보이기, rise·pop·fade·tap), `prefers-reduced-motion`이면 첫 장면에 멈춤, SVG 문서 틀.
- `scripts/motion/theme.py`: `services/viewer/src/renderer.py`의 `THEME_VARS_CSS`에서 `github-light`·`github-dark` 토큰을 읽는다. 다크의 `--primary-lighter`는 배경과 같아 강조 바탕으로 쓸 수 없으므로 primary와 배경을 섞어 만든다. 형광펜 색은 `meeting.css`의 값과 같게 둔다.
- `scripts/motion/hero.py`: README 소개 그림. 장면 넷(외부망에서 Markdown 작성 → Git으로 반입 → Viewer에서 읽고 회의·발표 → XWiki·OpenProject 동기화).
- `scripts/motion/help.py`: 도움말 그림 틀(왼쪽 단계 목록, 오른쪽 데스크톱 브라우저 창 안의 Viewer 화면)과 도움말 글·모음 생성.
- `scripts/motion/help_pages.py`: 메뉴 7개의 단계(4개씩)·화면·설명·팁. 화면 문구는 Viewer 코드의 실제 문구를 그대로 쓴다(`문서 검색`, `⌘K`, `회의 모드로 열기`, `+ 메모`, `발표하기`, `내려받기`, `원본 열기`, `슬라이드 미리보기`, `PDF 만들기`, `출판…` 등).
- `scripts/build_motion.py`: `docs/readme/hero-{light,dark}.svg`, `docs/help/img/<id>-{light,dark}.svg`, `docs/help/<id>.md`, `docs/help/README.md`를 만든다. 같은 입력이면 같은 바이트가 나온다.

GitHub가 SVG를 `<img>`로 보여 주므로 스크립트·외부 글꼴·외부 링크 없이 CSS 애니메이션만 쓴다. README와 도움말은 `<picture>`로 다크 모드 그림을 고른다.

## 문서

- 도움말 글은 생성 파일이며 머리에 「`python3 scripts/build_motion.py`로 생성, 문구는 `scripts/motion/help_pages.py`에서 고친다」는 주석을 단다. 각 글은 그림, 한두 문장 설명, 「이렇게 써요」 4단계, 「알아 두면 좋아요」, 도움말 모음·README 링크로 끝난다.
- README의 「버전별 주요 변경」은 「릴리스」 절로 옮기고 표 내용은 유지한다. 현재 버전 줄과 릴리스 링크를 남긴다.
- `docs/development.md`는 옛 README 본문을 옮기며 제목 단계와 상대 링크(`docs/…` → 같은 폴더 기준)를 고친다. 다른 문서가 README의 절을 가리키면 새 위치로 고친다.
- 「데이터는 어디에 있나요」: Git이 원본이고 XWiki·OpenProject는 파생 데이터, 회의 메모·형광펜·테마·최근 본 문서는 브라우저 localStorage, Outbound Feedback은 허용된 메타데이터만 서명된 메일로 반출.

## 검증

- `scripts/tests/test_motion.py`: 생성 결과가 커밋된 파일과 같다(오래된 그림·글 방지), 모든 SVG에 `<script`·`@import`·`http(s)://`(xmlns 제외)·외부 `href`가 없고 40KB 이하(참고한 두 저장소의 그림은 35KB까지), 줄임 대비(reduced-motion) 규칙이 있다, 단계는 메뉴마다 4개다.
- 같은 테스트에서 README·`docs/help/*.md`·`docs/development.md`의 상대 링크와 이미지 경로가 실제 파일을 가리키는지 확인한다.
- `scripts/test-all.sh` 전체 통과. 그림은 브라우저에서 라이트·다크로 한 번씩 열어 겹침·잘림을 눈으로 확인한다.
- PR을 연 뒤 Codex 검토(codex-review)로 확인하고, 지적을 반영한 뒤 병합한다.

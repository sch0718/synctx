# Phase 9 Chromium 브라우저 시험 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 이미 병합된 Phase 9 Viewer 렌더(`synctx-html` 블록, `mode=render`, `/render/{token}/…`)를 실제 Chromium에서 시험하는 선택 실행 pytest를 추가해 `docs/production-service-design.md` Phase 9 미완료 항목 중 로컬 자동화 가능한 부분을 닫는다.

**Architecture:** `services/viewer/tests/browser/`에 Playwright(Python) 시험을 둔다. 시스템 Chrome(`channel="chrome"`)을 쓰고, playwright 패키지나 Chrome이 없으면 모듈 전체를 skip한다. 기본 `pytest` 결과·런타임 이미지·`services/*` 의존성은 바꾸지 않는다. Viewer는 기존 시험 하네스처럼 임시 Git 저장소를 만들어 uvicorn을 백그라운드 스레드로 띄운다.

**Tech Stack:** pytest, playwright(루트 `pyproject.toml`의 별도 dependency group `browser`, dev 전용), 기존 FastAPI Viewer.

**Spec:** `docs/html-embed-design.md`(2·14장), `docs/production-service-design.md` Phase 9 "추가 설계: HTML 렌더 보기" 체크리스트.

## Global Constraints

- 런타임 코드·Dockerfile·Helm·이미지 태그는 건드리지 않는다. 시험과 dev 의존성, 문서만 바꾼다.
- 시험은 외부 네트워크에 접근하지 않는다(localhost만). 브라우저 바이너리를 내려받지 않는다(`playwright install` 금지, 시스템 Chrome 사용).
- 의존성은 별도 group `browser`에만 추가하고 기본 `dev` group에는 넣지 않는다. 기본 `uv run pytest`는 browser 시험을 skip하고 통과해야 한다.
- 다른 세션이 `services/sync`, `services/collab-gateway`, helm prometheusrule을 수정 중이므로 그 영역은 건드리지 않는다.
- 문서는 3종(development/architecture/production-service-design) 일관성 규칙을 따르되, 이 단위는 production-service-design 체크리스트와 `docs/local-development.md` 실행법만 갱신한다. Phase 번호는 바꾸지 않는다.
- Conventional Commits, 한국어 메시지.

## Review Focus

- Chrome 미설치·playwright 미설치 환경에서 collection이 깨지지 않고 skip된다.
- 토큰 만료·위조 시 원문 HTML이 실행되지 않는다(404/오류 화면에 스크립트 실행 흔적 없음).
- sandbox iframe 안 스크립트가 부모 `document`·쿠키·`localStorage`에 접근할 수 없다(`origin` null).
- iframe 안 `fetch`·`img src`로 외부 주소를 요청하면 CSP meta로 차단된다.
- 시험이 사용한 uvicorn·브라우저 프로세스가 실패 시에도 정리된다.

---

### Task 1: 하네스(선택 실행·서버 기동)

**Files:**
- Modify: `pyproject.toml` (`[dependency-groups]`에 `browser = ["playwright>=1.48"]`)
- Create: `services/viewer/tests/browser/conftest.py`
- Create: `services/viewer/tests/browser/test_harness_smoke.py`

**Interfaces:**
- Produces: fixture `viewer_server`(session scope) → `ViewerServer(base_url: str, repo_id: str)`; fixture `page`(function scope, 새 컨텍스트·페이지). 둘 다 playwright/Chrome 없으면 `pytest.skip`.

- [ ] **Step 1: 실패하는 시험** `test_viewer_serves_synctx_html_block`: 임시 Git 저장소에 `synctx-html` 블록이 든 `doc.md`와 `motion/index.html`(버튼 클릭 시 텍스트 변경)을 두고 `{base_url}/view/{repo_id}/doc.md`를 열면 iframe이 하나 있고 iframe 안 버튼 클릭 뒤 텍스트가 바뀐다.
- [ ] **Step 2:** 실행해 fixture 부재로 실패 확인: `uv run --group browser pytest services/viewer/tests/browser -v`
- [ ] **Step 3:** conftest 구현. 기존 `services/viewer/tests/test_standalone.py`의 실제 git clone 시험(137행 부근)과 `services/viewer/tests/conftest.py`의 app 로딩 방식을 따라 임시 저장소·설정(`html_embed.enabled`, `render_files` 켬)·uvicorn 스레드 기동을 만든다. 빈 포트를 고르고 종료 시 서버 shutdown·브라우저 close를 `finally`로 보장한다.
- [ ] **Step 4:** 통과 확인, 그리고 `uv run pytest services/viewer/tests -q`(group 없이)에서 browser 디렉터리가 skip되고 전체가 깨지지 않음을 확인.
- [ ] **Step 5:** 커밋 `test(viewer): Chromium 브라우저 시험 하네스를 선택 실행으로 추가한다`

### Task 2: 격리·CSP 시험

**Files:**
- Create: `services/viewer/tests/browser/test_embed_isolation.py`

**Interfaces:**
- Consumes: Task 1의 `viewer_server`, `page`.

- [ ] **Step 1: 시험 작성**(구현 코드 변경 없이 현행 동작을 고정한다)
  - `test_iframe_origin_is_opaque`: iframe 안에서 `location.origin`이 `"null"`이고 `window.parent.document` 접근이 SecurityError.
  - `test_iframe_cannot_read_viewer_storage`: 부모에 localStorage 값을 심고 iframe에서 읽히지 않는다.
  - `test_external_requests_blocked_by_csp`: iframe 안 스크립트가 `fetch("http://127.0.0.1:<다른 포트>/x")`와 외부 `img`를 시도하면 로컬 수신 서버에 요청이 도달하지 않고 `securitypolicyviolation`이 발생한다.
  - `test_without_csp_meta_request_is_not_blocked_control`: 대조군은 만들지 않는다(범위 밖). 대신 이 줄은 구현하지 않는다.
- [ ] **Step 2:** 실행. 실패하면 시험이 아니라 현행 코드의 결함 후보이므로 코드를 고치지 말고 결과를 보고한다.
- [ ] **Step 3:** 커밋 `test(viewer): synctx-html iframe 격리와 CSP 차단을 Chromium으로 확인한다`

### Task 3: 렌더 보기·토큰 시험

**Files:**
- Create: `services/viewer/tests/browser/test_render_mode.py`

- [ ] **Step 1: 시험 작성**
  - `test_render_mode_runs_html_and_relative_link_same_snapshot`: `.html`을 `?mode=render`로 열면 스크립트가 실행되고, 상대 링크 클릭 시 같은 ref·SHA의 `/render/{token}/…`로 열린다.
  - `test_tampered_token_returns_404_without_execution`: 토큰 한 글자를 바꾼 `/render/…`는 404이고 페이지 안 스크립트 실행 표식이 없다.
  - `test_expired_token_recovers`: `render_token_ttl_seconds`를 짧게 둔 서버에서 만료 뒤 같은 화면이 새 토큰으로 복구된다(설계 14장 기준. 복구 동작이 설계와 다르면 시험을 쓰지 말고 차이를 보고).
  - `test_block_zoom_overlay_esc_restores_focus`: `크게 보기` 클릭 → 오버레이 열림·iframe 재로드 없음(iframe 안 카운터 유지) → Esc로 닫히고 포커스가 버튼으로 복귀.
- [ ] **Step 2:** 실행·통과 확인. 안 되면 위와 같이 결함 후보로 보고.
- [ ] **Step 3:** 커밋 `test(viewer): HTML 렌더 보기 토큰·상대 링크·크게 보기를 Chromium으로 확인한다`

### Task 4: 문서

**Files:**
- Modify: `docs/production-service-design.md` (Phase 9 "추가 설계" 체크리스트의 `구현 후 Chromium 중첩 프레임…` 항목: 자동화한 시험 이름과 아직 안 한 것을 구분해 적는다. 독립 캐시 파드 2개·Edge·Firefox·Safari·운영 SSO는 미확인으로 남긴다)
- Modify: `docs/local-development.md` (브라우저 시험 실행법: `uv run --group browser pytest services/viewer/tests/browser`, 시스템 Chrome 필요, 기본 pytest에서는 skip)

- [ ] **Step 1:** 실제 실행 결과(통과·skip·결함 후보)를 그대로 반영해 문서 수정. 확인하지 않은 항목을 완료로 쓰지 않는다.
- [ ] **Step 2:** 커밋 `docs: Phase 9 Chromium 시험 범위와 남은 확인 항목을 갱신한다`

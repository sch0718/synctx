# XWiki REST API 레퍼런스

synctx의 XWiki 동기화 클라이언트 구현 시 참고할 API 레퍼런스.

## 1. 개요

XWiki는 페이지 CRUD를 위한 REST API를 제공한다.
XML-RPC는 7.1.4부터 폐기되었으므로 REST API만 사용한다.

| 항목 | 내용 |
|------|------|
| 프로토콜 | HTTPS (폐쇄망 내부에서는 HTTP도 가능) |
| 인증 | Basic Auth, API Token |
| 응답 포맷 | XML, JSON (Accept 헤더로 선택) |
| 배치 API | 없음 — 단건 호출을 병렬 실행 |
| Incoming Webhook | 없음 — pull 모델(cron/CI) 사용 |

## 2. 인증

### Basic Auth

```
Authorization: Basic base64(username:password)
```

```bash
curl -u "admin:password" "https://wiki.internal/rest/wikis/xwiki/spaces"
```

### API Token

XWiki 14.10+에서 사용자별 API 토큰 생성 가능.
사용자 프로필 → 인증 토큰 → 새 토큰 생성.

```
Authorization: Bearer <token>
```

### synctx에서의 인증 처리

```yaml
# .synctx.yaml
targets:
  xwiki:
    auth:
      type: basic              # basic | token
      credential_env: XWIKI_CREDENTIAL
      # basic: "username:password" 형태
      # token: 토큰 문자열
```

## 3. URL 구조

```
{base_url}/rest/wikis/{wiki}/spaces/{space}/pages/{page}
```

- `{base_url}`: XWiki 서버 URL (예: `https://wiki.internal`)
- `{wiki}`: 위키 이름 (기본: `xwiki`)
- `{space}`: 스페이스 이름 (중첩 가능: `spaces/{parent}/spaces/{child}`)
- `{page}`: 페이지 이름

### 중첩 스페이스

XWiki는 중첩 스페이스를 지원한다. URL에서 `/spaces/{name}`을 반복한다.

```
# 단일 스페이스
/rest/wikis/xwiki/spaces/아키텍처/pages/시스템-설계

# 중첩 스페이스 (아키텍처 > 백엔드)
/rest/wikis/xwiki/spaces/아키텍처/spaces/백엔드/pages/시스템-설계
```

synctx에서 디렉토리 깊이가 2 이상이면 중첩 스페이스로 매핑:

```
docs/아키텍처/백엔드/설계.md
  → spaces/아키텍처/spaces/백엔드/pages/설계
```

## 4. 페이지 API

### 4.1 페이지 조회 (GET)

```bash
curl -u "admin:password" \
  "https://wiki.internal/rest/wikis/xwiki/spaces/MySpace/pages/MyPage" \
  -H "Accept: application/json"
```

**응답 (JSON)**:
```json
{
  "id": "xwiki:MySpace.MyPage",
  "fullName": "MySpace.MyPage",
  "wiki": "xwiki",
  "space": "MySpace",
  "name": "MyPage",
  "title": "페이지 제목",
  "syntax": "xwiki/2.1",
  "content": "페이지 본문",
  "version": "3.1",
  "modified": "2026-06-19T10:30:00+09:00",
  "modifier": "admin"
}
```

**상태 코드**:
- `200` — 페이지 존재
- `404` — 페이지 없음

### 4.2 페이지 생성/수정 (PUT)

```bash
curl -u "admin:password" -X PUT \
  "https://wiki.internal/rest/wikis/xwiki/spaces/MySpace/pages/MyPage" \
  -H "Content-Type: application/xml" \
  -d '<page xmlns="http://www.xwiki.org">
        <title>문서 제목</title>
        <syntax>markdown/1.2</syntax>
        <content>## 마크다운 내용 그대로 입력</content>
      </page>'
```

**핵심 필드**:

| 필드 | 설명 | 비고 |
|------|------|------|
| `title` | 페이지 제목 | 필수 |
| `syntax` | 마크다운 구문 | `markdown/1.2` (Markdown 확장 설치 필요) 또는 `xwiki/2.1` |
| `content` | 페이지 본문 | 마크다운 원문 또는 XWiki 구문 |
| `parent` | 부모 페이지 | 선택 |

**상태 코드**:
- `201` — 새 페이지 생성됨
- `202` — 기존 페이지 수정됨

**XML 네임스페이스**: `xmlns="http://www.xwiki.org"`

### 4.3 페이지 삭제 (DELETE)

```bash
curl -u "admin:password" -X DELETE \
  "https://wiki.internal/rest/wikis/xwiki/spaces/MySpace/pages/MyPage"
```

synctx에서는 기본적으로 삭제하지 않고 마킹만 한다 (설정으로 변경 가능).

### 4.4 페이지 존재 확인

GET 요청의 상태 코드로 확인:

```python
response = client.get(page_url)
exists = response.status_code == 200
```

## 5. 첨부파일 API

### 5.1 첨부파일 업로드 (PUT)

```bash
curl -u "admin:password" -X PUT \
  "https://wiki.internal/rest/wikis/xwiki/spaces/MySpace/pages/MyPage/attachments/diagram.svg" \
  -H "Content-Type: image/svg+xml" \
  --data-binary @diagram.svg
```

**Content-Type 참고**:

| 확장자 | Content-Type |
|--------|-------------|
| `.svg` | `image/svg+xml` |
| `.png` | `image/png` |
| `.jpg` | `image/jpeg` |
| `.gif` | `image/gif` |
| `.pdf` | `application/pdf` |

### 5.2 첨부파일 목록 조회 (GET)

```bash
curl -u "admin:password" \
  "https://wiki.internal/rest/wikis/xwiki/spaces/MySpace/pages/MyPage/attachments" \
  -H "Accept: application/json"
```

### 5.3 첨부파일 참조

XWiki 마크다운에서 첨부파일 참조:

```markdown
![다이어그램](diagram.svg)
```

XWiki 구문에서:

```
[[image:diagram.svg]]
```

## 6. 스페이스 API

### 6.1 스페이스 목록 조회

```bash
curl -u "admin:password" \
  "https://wiki.internal/rest/wikis/xwiki/spaces" \
  -H "Accept: application/json"
```

### 6.2 스페이스 내 페이지 목록

```bash
curl -u "admin:password" \
  "https://wiki.internal/rest/wikis/xwiki/spaces/MySpace/pages" \
  -H "Accept: application/json"
```

## 7. Markdown 지원

### 방법 A: Markdown Syntax 확장 (권장)

XWiki에 Markdown Syntax 확장을 설치하면 마크다운 원문을 그대로 저장할 수 있다.

- **요구 사항**: XWiki 12.10+
- **설치**: XWiki 관리 > 확장 관리자 > "Markdown Syntax" 검색 > 설치
- **syntax 값**: `markdown/1.2`
- **장점**: 변환 불필요, 마크다운 원본 유지
- **단점**: 일부 XWiki 매크로 비호환, 확장 설치 필요 (관리자 작업)

### 방법 B: Pandoc 사전 변환

마크다운을 XWiki 네이티브 구문으로 변환한 후 저장한다.

```bash
pandoc -f markdown -t xwiki input.md -o output.xwiki
```

- **syntax 값**: `xwiki/2.1`
- **장점**: 확장 설치 불필요
- **단점**: 변환 단계 추가, Pandoc 의존성, 변환 품질 이슈 가능

### synctx 기본 전략

**방법 A를 기본으로 사용한다.** 마크다운 원본을 유지하면 외부에서의 편집과 동기화가 간편하다.
방법 B는 Markdown 확장 설치가 불가능한 환경을 위한 대안으로 지원한다.

```yaml
# .synctx.yaml
targets:
  xwiki:
    syntax: markdown/1.2    # markdown/1.2 | xwiki/2.1
    # xwiki/2.1 선택 시 pandoc 변환을 자동 수행
```

## 8. synctx 구현 시 고려 사항

### 8.1 페이지 생성 vs 수정 판단

```python
async def sync_page(client, space, page, content, title):
    if await client.page_exists(space, page):
        await client.put_page(space, page, content, title)  # 수정
    else:
        await client.put_page(space, page, content, title)  # 생성 (PUT은 idempotent)
```

XWiki REST API의 PUT은 idempotent하므로 존재 여부와 무관하게 동일한 호출을 사용할 수 있다.
다만 로깅이나 dry-run 출력을 위해 존재 여부를 먼저 확인하는 것이 좋다.

### 8.2 병렬 호출

배치 API가 없으므로 `asyncio` + `httpx.AsyncClient`로 병렬 호출한다.

```python
import asyncio
import httpx

async def sync_all(pages: list[SyncItem]):
    async with httpx.AsyncClient() as client:
        tasks = [sync_one(client, page) for page in pages]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    return results
```

동시 요청 수를 제한하여 XWiki 서버 부하를 방지한다 (`asyncio.Semaphore`).

### 8.3 마크다운 내 이미지 경로 처리

마크다운 문서에 상대 경로로 참조된 이미지는 XWiki 첨부파일로 업로드한 뒤,
본문의 이미지 경로를 XWiki 첨부파일 참조로 변환해야 한다.

```markdown
# 원본 (Git 저장소)
![아키텍처](images/architecture.svg)

# 변환 후 (XWiki markdown/1.2)
![아키텍처](architecture.svg)
# → 같은 페이지에 첨부된 파일을 참조
```

### 8.4 Mermaid 다이어그램 처리

XWiki의 Markdown 확장은 Mermaid 코드 블록을 자체적으로 렌더링하지 못한다.
두 가지 방법:

1. **SVG 변환 후 첨부** (권장): mermaid_to_svg.py로 SVG 생성 → 첨부파일 업로드 → 이미지 링크로 교체
2. **XWiki Mermaid 매크로**: XWiki에 Mermaid 매크로를 별도 설치하면 코드 블록 그대로 저장 가능 (단, 추가 확장 설치 필요)

### 8.5 한글 URL 인코딩

XWiki REST API URL에 한글 스페이스/페이지 이름을 사용할 때는 URL 인코딩이 필요하다.

```python
from urllib.parse import quote

space = quote("아키텍처", safe="")
page = quote("시스템-설계", safe="")
url = f"{base_url}/rest/wikis/xwiki/spaces/{space}/pages/{page}"
```

`httpx`는 URL 인코딩을 자동 처리하므로 직접 인코딩할 필요는 없을 수 있다.
구현 시 확인 필요.

## 9. 제약 사항 정리

| 항목 | 상태 | 대응 |
|------|------|------|
| Incoming Webhook | 없음 | cron 또는 GitLab CI hook으로 동기화 트리거 |
| 파일시스템 동기화 | 없음 | XWiki는 DB 기반 저장, REST API만 사용 |
| 배치 API | 없음 | 단건 PUT을 asyncio로 병렬 실행 |
| XML-RPC | 폐기됨 | REST API 전용 (7.1.4+) |
| Markdown 렌더링 | 확장 필요 | Markdown Syntax 확장 설치 (XWiki 12.10+) |
| Mermaid 렌더링 | 미지원 | SVG 변환 후 첨부파일로 업로드 |

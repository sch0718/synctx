# firebase-session 0.5.0 적용 (설계·계획)

작은 작업이라 설계와 계획을 한 문서에 둔다. 전제는 운영자 1명, 소수 지인, Cloudflare 무료다. 사용자에게 보이는 동작은 바꾸지 않는다.

## 왜 하나

`@sch0718/firebase-session@0.5.0`이 소셜 설정 조립(`socialLoginOptions`)과 시험용 ID 토큰 서명기(`/testing`의 `createTestTokens`)를 내보낸다. synctx의 `deploy/pages`에는 두 코드의 사본이 있다. synctx는 세션 API 라우트를 쓰지 않으므로 `handleSessionRoute`는 적용하지 않는다. 0.4.0과 0.5.0 사이에 깨지는 변경은 없다. 0.5.0은 로그인 본문의 `redirectUri`에 userinfo가 있으면 거절하지만, 클라이언트가 `location.origin`으로 주소를 만들어 synctx와는 상관없다.

## 바꾸는 것 (파일 순서대로)

1. `deploy/pages/package.json`·`package-lock.json`: `@sch0718/firebase-session`을 0.4.0에서 0.5.0으로 올린다(`npm install --save-exact`).
2. `deploy/pages/src/session.ts`의 `sessionAuthFor`: kakao·naver 조립(47-72행)을 아래 호출로 바꾼다.
   ```ts
   socialLoginOptions({ allowedAccounts: '', kakao: { clientId, clientSecret }, naver: { clientId, clientSecret }, redirectUris: `${origin}/auth/callback`, keys, fetch })
   ```
   - 동작은 같다.
     - 카카오는 시크릿이 없어도 켜지고, 네이버는 시크릿이 있어야 켜진다.
     - `allowedAccounts`의 빈 값은 패키지가 `undefined`로 바꾼다. `parseAccountList`가 둘 다 빈 집합으로 읽으므로 결과는 같다.
   - 차이 두 가지: ID·시크릿을 trim한다. 콜백 주소가 http이면서 localhost가 아니면 걸러진다. 운영은 https라 영향이 없다.
   - `defaultKeys` 지연 생성과 `deps.social`로 대체하는 경로는 그대로 둔다.
3. `deploy/pages/test/helpers.ts`의 `fakeGoogle()`: 직접 RSA 서명하던 코드를 `createTestTokens({ projectId: 'synctx-test' })`로 바꾼다.
   - 반환 모양 `{ certs, idToken(email) }`는 그대로 둔다. 사용처는 `test/auth.test.ts` 하나다.
   - `idToken(email)`은 `tokens.token({ email, sub: \`google-${email}\` })`이다.
   - 시험은 workerd(`@cloudflare/vitest-plugin`)에서 돈다. 그래서 `/testing`만 import하고 Node 전용 `/testing/d1`은 쓰지 않는다.
   - 쓰지 않게 된 `b64url` 도우미는 다른 데서 쓰지 않으면 지운다.

## 바꾸지 않는 것

- 로그인 화면, 클라이언트 `deviceLabel`. 랜드마크와 같은 코드지만 두 곳뿐이라 공용화하지 않는다.
- D1 시험 환경(miniflare)
- 진행 중인 다른 브랜치(`claude/static-auth-ui-5eea7f`)가 고치는 파일. 겹치는 파일은 없다.

## 검증

- `deploy/pages`에서 `npm run typecheck`, `npm test`, `npm run build:client`
- 저장소 전체 검사(`scripts/test-all.sh`)는 병합 전에 직접 돌린다. 이 저장소에는 PR CI가 없다(`static-export.yml`은 수동 실행이다).
- 사람 확인(배포 뒤): 실제 카카오·네이버 로그인

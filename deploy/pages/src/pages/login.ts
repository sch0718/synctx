import { DEFAULT_CSP } from '../http';
import { inlineJson, page } from './layout';

export interface FirebaseWebConfig {
  apiKey: string;
  authDomain: string;
  projectId: string;
  appId: string;
}

const DOMAIN_RE = /^[a-z0-9.-]+$/;

/** FIREBASE_WEB_CONFIG를 읽는다. 없거나 형식이 틀리면 null(로그인 화면은 503). */
export function parseFirebaseWebConfig(raw: string | undefined): FirebaseWebConfig | null {
  if (!raw) return null;
  let v: unknown;
  try {
    v = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof v !== 'object' || v === null) return null;
  const o = v as Record<string, unknown>;
  const keys = ['apiKey', 'authDomain', 'projectId', 'appId'] as const;
  if (!keys.every((k) => typeof o[k] === 'string' && (o[k] as string).trim())) return null;
  const cfg = Object.fromEntries(keys.map((k) => [k, (o[k] as string).trim()])) as unknown as FirebaseWebConfig;
  return DOMAIN_RE.test(cfg.authDomain) ? cfg : null;
}

/**
 * 로그인 화면 CSP. 기본 CSP에서 Firebase Google 로그인에 필요한 출처만 연다
 * (firebase-session `/vite`의 firebaseCsp와 같은 script-src·frame-src, Firebase Auth REST의 connect-src).
 * 공통 헤더 적용 함수(middleware)가 로그인 HTML에 이 값 하나만 넣는다.
 */
export function loginCsp(authDomain: string): string {
  return DEFAULT_CSP.replace("script-src 'self' 'unsafe-inline'", "script-src 'self' 'unsafe-inline' https://apis.google.com")
    .replace(
      "connect-src 'self'",
      "connect-src 'self' https://identitytoolkit.googleapis.com https://securetoken.googleapis.com",
    )
    .replace("frame-src 'none'", `frame-src https://${authDomain}`);
}

export interface LoginPageInput {
  firebase: FirebaseWebConfig;
  kakaoClientId?: string | undefined;
  naverClientId?: string | undefined;
  next: string;
}

const LOGIN_SCRIPT = '/view/static/synctx-auth/login.js';

export function loginPage(input: LoginPageInput): string {
  const button = (provider: string, label: string, enabled: boolean) =>
    `<button type="button" class="btn btn-primary" data-login="${provider}"${enabled ? '' : ' disabled'}>${label}</button>`;
  const config = {
    mode: 'login',
    next: input.next,
    firebase: input.firebase,
    kakao: input.kakaoClientId ? { clientId: input.kakaoClientId } : null,
    naver: input.naverClientId ? { clientId: input.naverClientId } : null,
  };
  return page(
    '로그인',
    '<h1 class="page-title">로그인</h1>' +
      '<p>문서를 보려면 로그인하세요. 로그인한 뒤에도 권한을 받은 저장소만 보입니다.</p>' +
      '<div class="login-buttons">' +
      button('google', 'Google로 로그인', true) +
      button('kakao', '카카오로 로그인', !!input.kakaoClientId) +
      button('naver', '네이버로 로그인', !!input.naverClientId) +
      '</div><p id="login-status" class="auth-muted" role="status" aria-live="polite"></p>' +
      inlineJson('synctx-login-config', config),
    { scripts: [LOGIN_SCRIPT] },
  );
}

/** 카카오·네이버가 돌려보내는 /auth/callback 화면. 로그인 JS가 코드를 세션으로 바꾼다. */
export function callbackPage(): string {
  return page(
    '로그인 중',
    '<h1 class="page-title">로그인 중</h1><p id="login-status" role="status" aria-live="polite">잠시 기다려 주세요.</p>' +
      '<p><a href="/login">로그인 화면으로</a></p>' +
      inlineJson('synctx-login-config', { mode: 'callback' }),
    { head: '<meta name="referrer" content="no-referrer">', scripts: [LOGIN_SCRIPT], themeSelect: false },
  );
}

export function unavailablePage(message: string): string {
  return page('잠시 이용할 수 없음', `<h1 class="page-title">잠시 이용할 수 없습니다</h1><p>${message}</p>`);
}

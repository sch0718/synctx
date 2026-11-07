// 로그인 화면(/login)과 카카오·네이버 콜백(/auth/callback). 세션은 서버가 HttpOnly 쿠키로 준다.
import { beginSocialSignIn, firebaseAuth, readSocialCallback, stripSocialCallback } from '@sch0718/firebase-session/client';

type Provider = 'kakao' | 'naver';
interface LoginConfig {
  mode: 'login' | 'callback';
  next?: string;
  firebase?: { apiKey: string; authDomain: string; projectId: string; appId: string };
  kakao?: { clientId: string } | null;
  naver?: { clientId: string } | null;
}

const NEXT_KEY = 'synctx:login-next';
const statusEl = document.getElementById('login-status');
const say = (text: string) => {
  if (statusEl) statusEl.textContent = text;
};

function safeNext(value: string | null | undefined): string {
  if (!value || !value.startsWith('/') || value.startsWith('//') || value.startsWith('/\\')) return '/view/';
  return /[\u0000-\u001f\u007f]/.test(value) ? '/view/' : value;
}

function readConfig(): LoginConfig | null {
  const el = document.getElementById('synctx-login-config');
  try {
    return el?.textContent ? (JSON.parse(el.textContent) as LoginConfig) : null;
  } catch {
    return null;
  }
}

/** 설정 화면 「로그인한 기기」에 보일 이름. landmark의 deviceLabel과 같은 규칙이다. */
function deviceLabel(ua: string): string {
  const device = /iPhone/i.test(ua)
    ? 'iPhone'
    : /iPad/i.test(ua)
      ? 'iPad'
      : /Android/i.test(ua)
        ? 'Android'
        : /Mac/i.test(ua)
          ? 'Mac'
          : /Windows/i.test(ua)
            ? 'Windows'
            : /Linux/i.test(ua)
              ? 'Linux'
              : '기기';
  const browser = /KAKAOTALK/i.test(ua)
    ? '카카오톡'
    : /NAVER\(inapp/i.test(ua)
      ? '네이버'
      : /Edg/i.test(ua)
        ? 'Edge'
        : /Chrome|CriOS/i.test(ua)
          ? 'Chrome'
          : /Firefox|FxiOS/i.test(ua)
            ? 'Firefox'
            : /Safari/i.test(ua)
              ? 'Safari'
              : '브라우저';
  return `${device} · ${browser}`.slice(0, 40);
}

async function post(path: string, body: unknown, headers: Record<string, string> = {}): Promise<Response> {
  return fetch(path, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...headers },
    body: JSON.stringify(body),
  });
}

async function failureText(res: Response): Promise<string> {
  if (res.status === 503 || res.status === 429) return '잠시 뒤 처음부터 다시 로그인해 주세요.';
  let code = '';
  try {
    code = String(((await res.json()) as { error?: unknown }).error ?? '');
  } catch {
    // 본문이 JSON이 아니면 상태만 알린다.
  }
  return `로그인하지 못했습니다(${code || res.status}). 다시 시도해 주세요.`;
}

async function googleSession(auth: ReturnType<typeof firebaseAuth>, idToken: string, next: string) {
  const res = await post('/auth/session', { label: deviceLabel(navigator.userAgent) }, { Authorization: `Bearer ${idToken}` });
  await auth.signOut().catch(() => undefined); // 세션 쿠키만 쓴다. Firebase 로그인 상태는 남기지 않는다.
  if (res.ok) location.assign(next);
  else say(await failureText(res));
}

async function loginMode(cfg: LoginConfig) {
  const next = safeNext(cfg.next);
  const auth = cfg.firebase ? firebaseAuth(cfg.firebase) : null;
  if (auth) {
    const back = await auth.redirectResult().catch(() => null);
    if (back) return googleSession(auth, back.idToken, next);
  }
  for (const btn of document.querySelectorAll<HTMLButtonElement>('[data-login]')) {
    btn.addEventListener('click', async () => {
      const provider = btn.dataset['login'];
      try {
        if (provider === 'google' && auth) {
          say('Google 로그인 창을 엽니다.');
          const result = await auth.signIn();
          if (result === 'redirect') return;
          if (!result) return say('로그인을 취소했습니다.');
          return googleSession(auth, result.idToken, next);
        }
        const social = provider === 'kakao' || provider === 'naver' ? cfg[provider] : null;
        if (social) {
          sessionStorage.setItem(NEXT_KEY, next);
          await beginSocialSignIn(provider as Provider, {
            clientId: social.clientId,
            redirectUri: `${location.origin}/auth/callback`,
          });
        }
      } catch {
        say('로그인하지 못했습니다. 다시 시도해 주세요.');
      }
    });
  }
}

async function callbackMode() {
  // 콜백 값은 다른 일보다 먼저 읽고 주소에서 지운다.
  const callback = readSocialCallback(location.href);
  history.replaceState(history.state, '', stripSocialCallback(location.href));
  const next = safeNext(sessionStorage.getItem(NEXT_KEY));
  sessionStorage.removeItem(NEXT_KEY);
  if (!callback) return say('진행 중인 로그인이 없습니다. 로그인 화면에서 다시 시작해 주세요.');
  if (!callback.ok) return say(callback.reason === 'cancelled' ? '로그인을 취소했습니다.' : '로그인하지 못했습니다. 다시 시도해 주세요.');
  const res = await post('/auth/session/social', {
    provider: callback.provider,
    code: callback.code,
    codeVerifier: callback.codeVerifier,
    redirectUri: callback.redirectUri,
    label: deviceLabel(navigator.userAgent),
  });
  if (res.ok) location.replace(next);
  else say(await failureText(res));
}

const cfg = readConfig();
if (cfg?.mode === 'callback') void callbackMode();
else if (cfg) void loginMode(cfg);

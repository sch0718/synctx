import {
  createSessionAuth,
  googleCerts,
  HttpError,
  socialKeys,
  socialLoginOptions,
  type AuthContext,
  type CertSource,
  type D1Database as PackageD1,
  type SessionAuth,
  type SocialLoginOptions,
} from '@sch0718/firebase-session/worker';
import type { Env } from './env';
import { normalizeIdentity } from './identity';

export const SESSION_COOKIE = '__Host-synctx_session';
/** 패키지 내부용 세션 헤더. 브라우저는 쿠키만 보낸다. */
export const SESSION_HEADER = 'X-Synctx-Session';
export const SESSION_MAX_AGE = 30 * 24 * 60 * 60;
const COOKIE_ATTRS = 'HttpOnly; Secure; SameSite=Lax; Path=/';

export function sessionCookie(sessionId: string): string {
  return `${SESSION_COOKIE}=${sessionId}; ${COOKIE_ATTRS}; Max-Age=${SESSION_MAX_AGE}`;
}

export function clearSessionCookie(): string {
  return `${SESSION_COOKIE}=; ${COOKIE_ATTRS}; Max-Age=0`;
}

export function readCookie(req: Request, name: string): string | null {
  for (const part of (req.headers.get('Cookie') ?? '').split(';')) {
    const eq = part.indexOf('=');
    if (eq > 0 && part.slice(0, eq).trim() === name) return part.slice(eq + 1).trim() || null;
  }
  return null;
}

export interface SessionDeps {
  certs?: CertSource;
  social?: Pick<SocialLoginOptions, 'keys' | 'fetch'>;
  now?: () => number;
}

let defaultCerts: CertSource | undefined;
let defaultKeys: SocialLoginOptions['keys'] | undefined;

export function sessionAuthFor(env: Env, req: Request, deps: SessionDeps = {}): SessionAuth {
  const social =
    env.KAKAO_CLIENT_ID || (env.NAVER_CLIENT_ID && env.NAVER_CLIENT_SECRET)
      ? socialLoginOptions({
          // 로그인 자체는 열어 둔다. 무엇을 볼 수 있는지는 D1 역할로 정한다.
          allowedAccounts: '',
          kakao: { clientId: env.KAKAO_CLIENT_ID, clientSecret: env.KAKAO_CLIENT_SECRET },
          naver: { clientId: env.NAVER_CLIENT_ID, clientSecret: env.NAVER_CLIENT_SECRET },
          redirectUris: `${new URL(req.url).origin}/auth/callback`,
          keys: deps.social?.keys ?? (defaultKeys ??= socialKeys()),
          fetch: deps.social?.fetch,
        })
      : undefined;
  return createSessionAuth({
    db: env.DB as unknown as PackageD1,
    projectId: env.FIREBASE_PROJECT_ID ?? '',
    allowedEmails: '',
    certs: deps.certs ?? (defaultCerts ??= googleCerts()),
    sessionHeader: SESSION_HEADER,
    googleSessionOnly: true,
    isAllowed: () => true,
    defaultLabel: '브라우저',
    now: deps.now ?? Date.now,
    random: (n) => crypto.getRandomValues(new Uint8Array(n)),
    uuid: () => crypto.randomUUID(),
    ...(social ? { social } : {}),
  });
}

export function identityOf(ctx: AuthContext): string {
  return normalizeIdentity(ctx.user.provider ? ctx.user.sub : ctx.user.email);
}

export interface Session {
  identity: string;
  ctx: AuthContext;
  auth: SessionAuth;
}

/**
 * 쿠키의 세션 id를 세션 헤더로 옮긴 요청으로 인증한다. Authorization 등 다른 헤더는 넘기지 않는다(세션 전용 경로).
 * 쿠키가 없거나 401·403이면 null. 인증서·D1 오류는 그대로 던진다(호출자가 503).
 */
export async function readSession(req: Request, env: Env, deps: SessionDeps = {}): Promise<Session | null> {
  const raw = readCookie(req, SESSION_COOKIE);
  if (!raw) return null;
  const auth = sessionAuthFor(env, req, deps);
  try {
    const ctx = await auth.authenticate(new Request(req.url, { headers: { [SESSION_HEADER]: raw } }));
    return { identity: identityOf(ctx), ctx, auth };
  } catch (e) {
    if (e instanceof HttpError && (e.status === 401 || e.status === 403)) return null;
    throw e;
  }
}

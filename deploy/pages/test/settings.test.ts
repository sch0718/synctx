import { env as testEnv } from 'cloudflare:test';
import { describe, expect, it } from 'vitest';
import { handleAuth } from '../src/auth';
import { handleSettings } from '../src/settings';
import { addMember, makeEnv, ORIGIN, OWNER, request, seedSession } from './helpers';

const OTHER_HASH = 'ab12cd34ef56' + '0'.repeat(52);

/** 같은 사람의 다른 기기 세션을 하나 더 만든다. 공개 id(해시 앞 12자)를 돌려준다. */
async function seedOtherDevice(identity: string): Promise<string> {
  const row = await testEnv.DB.prepare('SELECT user_id FROM sessions WHERE identity = ?').bind(identity).first<{ user_id: string }>();
  const now = Date.now();
  await testEnv.DB.prepare(
    'INSERT INTO sessions (id_hash, user_id, label, created_at, last_used_at, revoked_at, identity) VALUES (?, ?, ?, ?, ?, NULL, ?)',
  )
    .bind(OTHER_HASH, row!.user_id, 'iPhone · Safari', now, now, identity)
    .run();
  return OTHER_HASH.slice(0, 12);
}

function form(path: string, fields: Record<string, string>, session: string, origin = ORIGIN): Request {
  return request(path, {
    method: 'POST',
    session,
    headers: { Origin: origin, 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams(fields).toString(),
  });
}

const revokedAt = async () =>
  (await testEnv.DB.prepare('SELECT revoked_at FROM sessions WHERE id_hash = ?').bind(OTHER_HASH).first<{ revoked_at: number | null }>())
    ?.revoked_at;

describe('GET /settings', () => {
  it('계정·볼 수 있는 저장소·기기·로그아웃을 보이고, 관리 링크는 관리할 사람에게만', async () => {
    await addMember('repo-a', 'viewer@example.com', 'viewer');
    const session = await seedSession('viewer@example.com');
    await seedOtherDevice('viewer@example.com');
    const res = await handleSettings(request('/settings', { session }), makeEnv());
    expect(res.status).toBe(200);
    const html = await res.text();
    expect(html).toContain('Google로 로그인했습니다');
    expect(html).toContain('viewer@example.com');
    expect(html).toContain('<a href="/view/repo-a/">A 저장소</a>');
    expect(html).not.toContain('B 저장소');
    expect(html).not.toContain('href="/admin"');
    expect(html).toContain('이 기기');
    expect(html).toContain('iPhone · Safari');
    expect(html).toContain('action="/auth/logout"');
  });

  it('담당자·전체 관리자에게는 관리 링크, 전체 관리자에게는 모든 저장소', async () => {
    await addMember('repo-b', 'manager@example.com', 'manager');
    const manager = await (await handleSettings(request('/settings', { session: await seedSession('manager@example.com') }), makeEnv())).text();
    expect(manager).toContain('href="/admin"');
    const owner = await (await handleSettings(request('/settings', { session: await seedSession(OWNER) }), makeEnv())).text();
    expect(owner).toContain('A 저장소');
    expect(owner).toContain('B 저장소');
  });

  it('카카오 identity는 카카오로 보인다', async () => {
    const html = await (await handleSettings(request('/settings', { session: await seedSession('kakao:123') }), makeEnv())).text();
    expect(html).toContain('카카오로 로그인했습니다');
    expect(html).toContain('아직 볼 수 있는 저장소가 없습니다');
  });

  it('세션이 없으면 로그인으로 보낸다', async () => {
    const res = await handleSettings(request('/settings'), makeEnv());
    expect(res.status).toBe(302);
    expect(res.headers.get('Location')).toBe('/login?next=%2Fsettings');
  });
});

describe('POST /auth/sessions/revoke', () => {
  it('다른 기기 하나를 로그아웃시키고 설정으로 돌아간다. 두 번 눌러도 설정으로', async () => {
    const session = await seedSession('viewer@example.com');
    const id = await seedOtherDevice('viewer@example.com');
    const res = await handleAuth(form('/auth/sessions/revoke', { id }, session), makeEnv());
    expect(res.status).toBe(303);
    expect(res.headers.get('Location')).toBe('/settings');
    expect(await revokedAt()).not.toBeNull();
    const again = await handleAuth(form('/auth/sessions/revoke', { id }, session), makeEnv());
    expect(again.headers.get('Location')).toBe('/settings');
    // 이 기기 세션은 그대로다.
    expect((await handleSettings(request('/settings', { session }), makeEnv())).status).toBe(200);
  });

  it('target=others는 이 기기를 뺀 전부를 로그아웃시킨다', async () => {
    const session = await seedSession('viewer@example.com');
    await seedOtherDevice('viewer@example.com');
    await handleAuth(form('/auth/sessions/revoke', { target: 'others' }, session), makeEnv());
    expect(await revokedAt()).not.toBeNull();
    expect((await handleSettings(request('/settings', { session }), makeEnv())).status).toBe(200);
  });

  it('Origin이 다르면 403이고 아무것도 철회하지 않는다', async () => {
    const session = await seedSession('viewer@example.com');
    const id = await seedOtherDevice('viewer@example.com');
    const res = await handleAuth(form('/auth/sessions/revoke', { id }, session, 'https://evil.test'), makeEnv());
    expect(res.status).toBe(403);
    expect(await revokedAt()).toBeNull();
  });
});

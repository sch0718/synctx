import { env as testEnv } from 'cloudflare:test';
import { describe, expect, it } from 'vitest';
import { handleAdminApi } from '../src/admin/api';
import { acceptInvite, createInvite, INVITE_TTL } from '../src/admin/invites';
import { handleInvite } from '../src/invite';
import { addMember, makeEnv, ORIGIN, OWNER, post, request, seedSession } from './helpers';

const db = () => testEnv.DB;
const count = async (sql: string, ...binds: unknown[]) =>
  (await db().prepare(`SELECT COUNT(*) AS n FROM (${sql})`).bind(...binds).first<{ n: number }>())!.n;
const owner = { identity: OWNER, owner: true };

async function invite(role: 'viewer' | 'manager' | 'admin', repoId: string | null = 'repo-a', now = Date.now()) {
  const created = await createInvite(db(), { repoId, role, actor: owner, now });
  return created!.token;
}

describe('초대 만들기', () => {
  it('담당자는 자기 저장소 초대만, 토큰은 해시만 저장한다', async () => {
    await addMember('repo-a', 'm@example.com', 'manager');
    const m = await seedSession('m@example.com');
    const res = await handleAdminApi(post('/api/admin/repos/repo-a/invites', { role: 'manager' }, { session: m }), makeEnv());
    expect(res.status).toBe(200);
    const { url, expiresAt } = (await res.json()) as { url: string; expiresAt: number };
    const token = url.slice(`${ORIGIN}/invite/`.length);
    expect(token).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(expiresAt - Date.now()).toBeGreaterThan(INVITE_TTL - 60_000);
    expect(await count('SELECT 1 FROM invites WHERE token_hash = ?', token)).toBe(0);
    expect(await count("SELECT 1 FROM audit_log WHERE action = 'invite.create'")).toBe(1);
    const other = await handleAdminApi(post('/api/admin/repos/repo-b/invites', { role: 'viewer' }, { session: m }), makeEnv());
    expect(other.status).toBe(403);
    expect(await count("SELECT 1 FROM audit_log WHERE action = 'invite.create'")).toBe(1);
  });

  it('관리자 초대는 전체 관리자만', async () => {
    await addMember('repo-a', 'm@example.com', 'manager');
    const env = makeEnv();
    expect((await handleAdminApi(post('/api/admin/admin-invites', {}, { session: await seedSession('m@example.com') }), env)).status).toBe(403);
    expect((await handleAdminApi(post('/api/admin/admin-invites', {}, { session: await seedSession(OWNER) }), env)).status).toBe(200);
  });
});

describe('초대 수락', () => {
  it('1회용: 수락하면 역할을 넣고, 두 번째는 같은 사람이어도 거절', async () => {
    const token = await invite('viewer');
    expect((await acceptInvite(db(), token, 'a@example.com', Date.now())).status).toBe('ok');
    expect((await acceptInvite(db(), token, 'a@example.com', Date.now())).status).toBe('used');
    expect((await acceptInvite(db(), token, 'b@example.com', Date.now())).status).toBe('used');
    expect(await count("SELECT 1 FROM repo_members WHERE repo_id = 'repo-a'")).toBe(1);
    expect(await count("SELECT 1 FROM audit_log WHERE action = 'invite.accept'")).toBe(1);
  });

  it('만료된 초대·모르는 토큰은 거절하고 아무것도 바꾸지 않는다', async () => {
    const expired = await invite('viewer', 'repo-a', Date.now() - INVITE_TTL - 1);
    expect((await acceptInvite(db(), expired, 'a@example.com', Date.now())).status).toBe('expired');
    expect((await acceptInvite(db(), 'x'.repeat(43), 'a@example.com', Date.now())).status).toBe('invalid');
    expect((await acceptInvite(db(), 'short', 'a@example.com', Date.now())).status).toBe('invalid');
    expect(await count('SELECT 1 FROM repo_members')).toBe(0);
    expect(await count('SELECT 1 FROM invites WHERE used_at IS NOT NULL')).toBe(0);
  });

  it.each([
    ['서로 다른 사람', 'a@example.com', 'b@example.com'],
    ['같은 사람', 'a@example.com', 'a@example.com'],
  ])('동시 수락(%s)은 정확히 하나만 성공한다', async (_label, first, second) => {
    const token = await invite('viewer');
    const results = await Promise.all([first, second].map((who) => acceptInvite(db(), token, who, Date.now())));
    expect(results.filter((r) => r.status === 'ok')).toHaveLength(1);
    expect(await count("SELECT 1 FROM repo_members WHERE repo_id = 'repo-a'")).toBe(1);
    expect(await count("SELECT 1 FROM audit_log WHERE action = 'invite.accept'")).toBe(1);
  });

  it('수락은 역할을 낮추지 않는다(마지막 담당자가 viewer 초대를 받아도 담당자)', async () => {
    await addMember('repo-a', 'm@example.com', 'manager');
    const token = await invite('viewer');
    expect((await acceptInvite(db(), token, 'm@example.com', Date.now())).status).toBe('ok');
    expect(await count("SELECT 1 FROM repo_members WHERE repo_id = 'repo-a' AND role = 'manager'")).toBe(1);
  });

  it('관리자 초대는 site_roles에 넣는다', async () => {
    const token = await invite('admin', null);
    expect((await acceptInvite(db(), token, 'kakao:Xy', Date.now())).status).toBe('ok');
    expect(await count("SELECT 1 FROM site_roles WHERE identity = 'kakao:Xy'")).toBe(1);
  });

  it('감사 기록이 실패하면 소비·역할 반영도 되돌린다', async () => {
    const token = await invite('viewer');
    await db().prepare('DROP TABLE audit_log').run();
    try {
      await expect(acceptInvite(db(), token, 'a@example.com', Date.now())).rejects.toThrow();
      expect(await count('SELECT 1 FROM invites WHERE used_at IS NOT NULL OR used_by IS NOT NULL')).toBe(0);
      expect(await count('SELECT 1 FROM repo_members')).toBe(0);
    } finally {
      await db().prepare('CREATE TABLE audit_log (at INTEGER NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL, detail TEXT)').run();
    }
  });

  it('역할 반영이 실패하면 소비도 되돌린다', async () => {
    const token = await invite('viewer');
    await db().prepare('DROP TABLE repo_members').run();
    try {
      await expect(acceptInvite(db(), token, 'a@example.com', Date.now())).rejects.toThrow();
      expect(await count('SELECT 1 FROM invites WHERE used_at IS NOT NULL OR used_by IS NOT NULL')).toBe(0);
      expect(await count("SELECT 1 FROM audit_log WHERE action = 'invite.accept'")).toBe(0);
    } finally {
      await db().batch([
        db().prepare(
          `CREATE TABLE repo_members (repo_id TEXT NOT NULL, identity TEXT NOT NULL, role TEXT NOT NULL CHECK (role IN ('manager', 'viewer')),
           added_by TEXT NOT NULL, added_at INTEGER NOT NULL, PRIMARY KEY (repo_id, identity))`,
        ),
        db().prepare('CREATE INDEX repo_members_identity ON repo_members(identity)'),
      ]);
    }
  });
});

describe('/invite/<token> 화면', () => {
  it('로그인 전에는 로그인으로, 로그인 뒤 수락 버튼, POST로 수락', async () => {
    const env = makeEnv();
    const token = await invite('viewer');
    const login = await handleInvite(request(`/invite/${token}`), env, token);
    expect(login.status).toBe(302);
    expect(login.headers.get('Location')).toBe(`/login?next=${encodeURIComponent(`/invite/${token}`)}`);
    const session = await seedSession('a@example.com');
    const page = await handleInvite(request(`/invite/${token}`, { session }), env, token);
    expect(page.status).toBe(200);
    expect(page.headers.get('Referrer-Policy')).toBe('no-referrer');
    expect(await page.text()).toContain('A 저장소');
    expect((await handleInvite(post(`/invite/${token}`, {}, { session, origin: 'https://evil.test' }), env, token)).status).toBe(403);
    const accepted = await handleInvite(post(`/invite/${token}`, {}, { session }), env, token);
    expect(accepted.status).toBe(303);
    expect(accepted.headers.get('Location')).toBe('/view/repo-a/');
    expect((await handleInvite(request(`/invite/${token}`, { session }), env, token)).status).toBe(410);
  });
});

import { env as testEnv } from 'cloudflare:test';
import { describe, expect, it } from 'vitest';
import { handleAdminApi } from '../src/admin/api';
import { handleAdminPage } from '../src/admin/page';
import { addMember, makeEnv, OWNER, post, request, seedSession } from './helpers';

const db = () => testEnv.DB;
const call = (req: Request) => handleAdminApi(req, makeEnv());
const role = async (repo: string, who: string) =>
  (await db().prepare('SELECT role FROM repo_members WHERE repo_id = ? AND identity = ?').bind(repo, who).first<{ role: string }>())?.role ?? null;
const audits = async (action: string) =>
  (await db().prepare('SELECT COUNT(*) AS n FROM audit_log WHERE action = ?').bind(action).first<{ n: number }>())!.n;
const managers = async (repo: string) =>
  (await db().prepare("SELECT COUNT(*) AS n FROM repo_members WHERE repo_id = ? AND role = 'manager'").bind(repo).first<{ n: number }>())!.n;
const del = (path: string, body: unknown, session: string) => post(path, body, { method: 'DELETE', session });

describe('역할별 허용·거절', () => {
  it('담당자는 자기 저장소만 편집하고, 열람자·다른 저장소 담당자·로그인 안 한 사람은 거절', async () => {
    await addMember('repo-a', 'm@example.com', 'manager');
    await addMember('repo-b', 'mb@example.com', 'manager');
    await addMember('repo-a', 'v@example.com', 'viewer');
    const add = (session?: string) => post('/api/admin/repos/repo-a/members', { identity: 'New@Example.com', role: 'viewer' }, { session });
    expect((await call(add(await seedSession('v@example.com')))).status).toBe(403);
    expect((await call(add(await seedSession('mb@example.com')))).status).toBe(403);
    expect((await call(add())).status).toBe(401);
    expect(await audits('member.set')).toBe(0);
    expect((await call(add(await seedSession('m@example.com')))).status).toBe(200);
    expect(await role('repo-a', 'new@example.com')).toBe('viewer');
    expect(await audits('member.set')).toBe(1);
    const list = await call(request('/api/admin/repos/repo-a/members', { session: await seedSession('m@example.com') }));
    expect(((await list.json()) as { members: unknown[] }).members).toHaveLength(3);
    expect((await call(request('/api/admin/repos/repo-a/members', { session: await seedSession('v@example.com') }))).status).toBe(403);
  });

  it('전체 관리자는 담당자가 없는 저장소도 편집하고 관리자·변경 기록을 다룬다', async () => {
    const owner = await seedSession(OWNER);
    expect((await call(post('/api/admin/repos/repo-b/members', { identity: 'kakao:Abc', role: 'manager' }, { session: owner }))).status).toBe(200);
    expect(await role('repo-b', 'kakao:Abc')).toBe('manager');
    expect((await call(post('/api/admin/admins', { identity: 'adm@example.com' }, { session: owner }))).status).toBe(200);
    const admins = (await (await call(request('/api/admin/admins', { session: owner }))).json()) as { admins: { identity: string }[] };
    expect(admins.admins.map((a) => a.identity)).toEqual(['adm@example.com']);
    const audit = (await (await call(request('/api/admin/audit', { session: owner }))).json()) as { entries: { action: string }[] };
    expect(audit.entries.map((e) => e.action).sort()).toEqual(['admin.add', 'member.set']);
    expect((await call(del('/api/admin/admins', { identity: 'adm@example.com' }, owner))).status).toBe(200);
  });

  it('관리자·변경 기록·관리자 초대는 전체 관리자만', async () => {
    await db().prepare("INSERT INTO site_roles VALUES ('adm@example.com', 'admin', ?, 0)").bind(OWNER).run();
    await addMember('repo-a', 'm@example.com', 'manager');
    for (const who of ['adm@example.com', 'm@example.com']) {
      const session = await seedSession(who);
      expect((await call(request('/api/admin/admins', { session }))).status).toBe(403);
      expect((await call(request('/api/admin/audit', { session }))).status).toBe(403);
      expect((await call(post('/api/admin/admins', { identity: 'x@example.com' }, { session }))).status).toBe(403);
      expect((await call(post('/api/admin/admin-invites', {}, { session }))).status).toBe(403);
    }
  });

  it('me는 담당 저장소만 돌려준다', async () => {
    await addMember('repo-a', 'm@example.com', 'manager');
    await addMember('repo-b', 'm@example.com', 'viewer');
    const me = (await (await call(request('/api/admin/me', { session: await seedSession('m@example.com') }))).json()) as { repos: { id: string }[]; owner: boolean };
    expect(me.repos.map((r) => r.id)).toEqual(['repo-a']);
    expect(me.owner).toBe(false);
    const all = (await (await call(request('/api/admin/me', { session: await seedSession(OWNER) }))).json()) as { repos: unknown[] };
    expect(all.repos).toHaveLength(2);
  });

  it('모르는 repo_id는 404, 틀린 입력은 400, Origin이 다르면 403', async () => {
    const owner = await seedSession(OWNER);
    expect((await call(post('/api/admin/repos/nope/members', { identity: 'a@example.com', role: 'viewer' }, { session: owner }))).status).toBe(404);
    expect((await call(post('/api/admin/repos/nope/invites', { role: 'viewer' }, { session: owner }))).status).toBe(404);
    expect((await call(post('/api/admin/repos/repo-a/members', { identity: 'not an id', role: 'viewer' }, { session: owner }))).status).toBe(400);
    expect((await call(post('/api/admin/repos/repo-a/members', { identity: 'a@example.com', role: 'owner' }, { session: owner }))).status).toBe(400);
    expect((await call(post('/api/admin/repos/repo-a/members', { identity: 'a@example.com', role: 'viewer' }, { session: owner, origin: 'https://evil.test' }))).status).toBe(403);
    expect(await audits('member.set')).toBe(0);
  });
});

describe('마지막 담당자', () => {
  it('담당자는 마지막 담당자(자기 자신)를 지우거나 열람자로 바꾸지 못한다', async () => {
    await addMember('repo-a', 'm@example.com', 'manager');
    const m = await seedSession('m@example.com');
    expect((await call(del('/api/admin/repos/repo-a/members', { identity: 'm@example.com' }, m))).status).toBe(409);
    expect((await call(post('/api/admin/repos/repo-a/members', { identity: 'm@example.com', role: 'viewer' }, { session: m }))).status).toBe(409);
    expect(await role('repo-a', 'm@example.com')).toBe('manager');
    expect(await audits('member.remove') + (await audits('member.set'))).toBe(0);
  });

  it('전체 관리자는 마지막 담당자도 지운다', async () => {
    await addMember('repo-a', 'm@example.com', 'manager');
    expect((await call(del('/api/admin/repos/repo-a/members', { identity: 'm@example.com' }, await seedSession(OWNER)))).status).toBe(200);
    expect(await managers('repo-a')).toBe(0);
  });

  it.each([
    ['삭제/삭제', 'remove', 'remove'],
    ['강등/강등', 'demote', 'demote'],
    ['삭제/강등', 'remove', 'demote'],
  ])('담당자 둘이 서로를 동시에 %s 해도 한 명은 남는다', async (_label, first, second) => {
    await addMember('repo-a', 'm1@example.com', 'manager');
    await addMember('repo-a', 'm2@example.com', 'manager');
    const s1 = await seedSession('m1@example.com');
    const s2 = await seedSession('m2@example.com');
    const act = (kind: string, session: string, target: string) =>
      kind === 'remove'
        ? call(del('/api/admin/repos/repo-a/members', { identity: target }, session))
        : call(post('/api/admin/repos/repo-a/members', { identity: target, role: 'viewer' }, { session }));
    const results = await Promise.all([act(first, s1, 'm2@example.com'), act(second, s2, 'm1@example.com')]);
    expect(results.map((r) => r.status).sort()).toEqual([200, 403]);
    expect(await managers('repo-a')).toBe(1);
    expect((await audits('member.remove')) + (await audits('member.set'))).toBe(1);
  });

  it('감사 기록이 실패하면 담당자 변경도 되돌린다', async () => {
    await addMember('repo-a', 'm@example.com', 'manager');
    await addMember('repo-a', 'v@example.com', 'viewer');
    await db().prepare('DROP TABLE audit_log').run();
    try {
      await expect(call(del('/api/admin/repos/repo-a/members', { identity: 'v@example.com' }, await seedSession('m@example.com')))).rejects.toThrow();
      expect(await role('repo-a', 'v@example.com')).toBe('viewer');
    } finally {
      await db().prepare('CREATE TABLE audit_log (at INTEGER NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL, detail TEXT)').run();
    }
  });
});

describe('관리 화면', () => {
  it('로그인하지 않으면 로그인으로, 역할이 없으면 403, 담당자는 200', async () => {
    const env = makeEnv();
    expect((await handleAdminPage(request('/admin'), env)).status).toBe(302);
    expect((await handleAdminPage(request('/admin', { session: await seedSession('v@example.com') }), env)).status).toBe(403);
    await addMember('repo-a', 'm@example.com', 'manager');
    const ok = await handleAdminPage(request('/admin', { session: await seedSession('m@example.com') }), env);
    expect(ok.status).toBe(200);
    expect(await ok.text()).toContain('/view/static/synctx-auth/admin.js');
  });

  it('열 때 만료 초대를 지운다', async () => {
    await db().prepare("INSERT INTO invites (token_hash, repo_id, role, expires_at, created_by) VALUES ('h', 'repo-a', 'viewer', 1, ?)").bind(OWNER).run();
    await handleAdminPage(request('/admin', { session: await seedSession(OWNER) }), makeEnv());
    expect(await db().prepare('SELECT * FROM invites').first()).toBeNull();
  });
});

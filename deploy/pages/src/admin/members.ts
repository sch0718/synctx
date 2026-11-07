import type { RepoRole } from '../authz';
import { auditAfterChange } from './audit';

export type ChangeResult = 'ok' | 'forbidden' | 'last-manager' | 'not-found';

export interface Actor {
  identity: string;
  /** 전체 관리자(OWNER_ACCOUNTS)면 담당자 확인과 마지막 담당자 규칙을 건너뛴다 */
  owner: boolean;
}

export interface MemberRow {
  identity: string;
  role: RepoRole;
  added_by: string;
  added_at: number;
}

export async function listMembers(db: D1Database, repoId: string): Promise<MemberRow[]> {
  const { results } = await db
    .prepare(
      `SELECT identity, role, added_by, added_at FROM repo_members WHERE repo_id = ?
       ORDER BY CASE role WHEN 'manager' THEN 0 ELSE 1 END, identity`,
    )
    .bind(repoId)
    .all<MemberRow>();
  return results;
}

/** 거절된 변경의 이유(표시용). 판정 자체는 변경 SQL이 했다. */
async function whyNot(db: D1Database, repoId: string, actor: Actor, identity: string): Promise<ChangeResult> {
  if (!actor.owner) {
    const mine = await db
      .prepare("SELECT 1 FROM repo_members WHERE repo_id = ? AND identity = ? AND role = 'manager'")
      .bind(repoId, actor.identity)
      .first();
    if (!mine) return 'forbidden';
  }
  const target = await db
    .prepare('SELECT role FROM repo_members WHERE repo_id = ? AND identity = ?')
    .bind(repoId, identity)
    .first<{ role: RepoRole }>();
  return target?.role === 'manager' ? 'last-manager' : 'not-found';
}

// 요청자 권한(?owner = 1 또는 그 저장소 담당자)과 마지막 담당자 보호를 변경 SQL 안에서 다시 확인한다.
// D1은 쓰기를 차례로 처리하고 batch는 한 트랜잭션이라, 동시에 온 두 요청도 앞 요청의 결과를 보고 판정한다.
const ACTOR_IS_MANAGER = `(?owner = 1 OR EXISTS (SELECT 1 FROM repo_members a
  WHERE a.repo_id = ?repo AND a.identity = ?actor AND a.role = 'manager'))`;
const ANOTHER_MANAGER = `EXISTS (SELECT 1 FROM repo_members o
  WHERE o.repo_id = ?repo AND o.role = 'manager' AND o.identity <> ?target)`;

function named(sql: string, names: string[]): string {
  // D1은 ?NNN 번호 매개변수를 받는다. 읽기 쉽게 쓴 이름을 번호로 바꾼다.
  return names.reduce((s, name, i) => s.replaceAll(`?${name}`, `?${i + 1}`), sql);
}

const SET_SQL = named(
  `INSERT INTO repo_members (repo_id, identity, role, added_by, added_at)
   SELECT ?repo, ?target, ?role, ?actor, ?now
   WHERE ${ACTOR_IS_MANAGER} AND (?role = 'manager' OR ?owner = 1 OR ${ANOTHER_MANAGER})
   ON CONFLICT (repo_id, identity) DO UPDATE SET role = excluded.role, added_by = excluded.added_by, added_at = excluded.added_at`,
  ['repo', 'target', 'role', 'actor', 'now', 'owner'],
);

const REMOVE_SQL = named(
  `DELETE FROM repo_members WHERE repo_id = ?repo AND identity = ?target
   AND ${ACTOR_IS_MANAGER} AND (?owner = 1 OR role <> 'manager' OR ${ANOTHER_MANAGER})`,
  ['repo', 'target', 'actor', 'owner'],
);

/** 열람자·담당자 지정(새로 넣거나 역할을 덮어쓴다). 전체 관리자가 아니면 마지막 담당자를 viewer로 바꾸지 못한다. */
export async function setMember(
  db: D1Database,
  m: { repoId: string; identity: string; role: RepoRole; actor: Actor; now: number },
): Promise<ChangeResult> {
  const [change] = await db.batch([
    db.prepare(SET_SQL).bind(m.repoId, m.identity, m.role, m.actor.identity, m.now, m.actor.owner ? 1 : 0),
    auditAfterChange(db, { at: m.now, actor: m.actor.identity, action: 'member.set', target: m.repoId, detail: { identity: m.identity, role: m.role } }),
  ]);
  return change!.meta.changes > 0 ? 'ok' : whyNot(db, m.repoId, m.actor, m.identity);
}

/** 열람자·담당자 삭제. 전체 관리자가 아니면 마지막 담당자를 지우지 못한다. */
export async function removeMember(
  db: D1Database,
  m: { repoId: string; identity: string; actor: Actor; now: number },
): Promise<ChangeResult> {
  const [change] = await db.batch([
    db.prepare(REMOVE_SQL).bind(m.repoId, m.identity, m.actor.identity, m.actor.owner ? 1 : 0),
    auditAfterChange(db, { at: m.now, actor: m.actor.identity, action: 'member.remove', target: m.repoId, detail: { identity: m.identity } }),
  ]);
  return change!.meta.changes > 0 ? 'ok' : whyNot(db, m.repoId, m.actor, m.identity);
}

export interface AdminRow {
  identity: string;
  added_by: string;
  added_at: number;
}

export async function listAdmins(db: D1Database): Promise<AdminRow[]> {
  const { results } = await db
    .prepare('SELECT identity, added_by, added_at FROM site_roles ORDER BY identity')
    .all<AdminRow>();
  return results;
}

/** 관리자 지정·해제는 전체 관리자만 한다(호출자가 확인). */
export async function setAdmin(db: D1Database, identity: string, actor: string, now: number): Promise<boolean> {
  const [change] = await db.batch([
    db
      .prepare(
        `INSERT INTO site_roles (identity, role, added_by, added_at) VALUES (?, 'admin', ?, ?)
         ON CONFLICT (identity) DO NOTHING`,
      )
      .bind(identity, actor, now),
    auditAfterChange(db, { at: now, actor, action: 'admin.add', target: identity }),
  ]);
  return change!.meta.changes > 0;
}

export async function removeAdmin(db: D1Database, identity: string, actor: string, now: number): Promise<boolean> {
  const [change] = await db.batch([
    db.prepare('DELETE FROM site_roles WHERE identity = ?').bind(identity),
    auditAfterChange(db, { at: now, actor, action: 'admin.remove', target: identity }),
  ]);
  return change!.meta.changes > 0;
}

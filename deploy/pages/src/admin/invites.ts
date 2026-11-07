import { base64url, isCapability, sha256Hex } from '@sch0718/firebase-session/worker';
import { auditAfterChange } from './audit';
import type { Actor } from './members';

export const INVITE_TTL = 7 * 24 * 60 * 60 * 1000;
export type InviteRole = 'admin' | 'manager' | 'viewer';

const CREATE_SQL = `INSERT INTO invites (token_hash, repo_id, role, expires_at, created_by)
  SELECT ?1, ?2, ?3, ?4, ?5
  WHERE ?6 = 1 OR EXISTS (SELECT 1 FROM repo_members WHERE repo_id = ?2 AND identity = ?5 AND role = 'manager')`;

/**
 * 1회용 초대(7일). 토큰 원문은 돌려주기만 하고 D1에는 SHA-256만 둔다.
 * 저장소 초대는 그 저장소 담당자·전체 관리자, 관리자 초대(repoId null)는 전체 관리자만(호출자가 확인) 만든다.
 */
export async function createInvite(
  db: D1Database,
  i: { repoId: string | null; role: InviteRole; actor: Actor; now: number },
): Promise<{ token: string; expiresAt: number } | null> {
  const token = base64url(crypto.getRandomValues(new Uint8Array(32)));
  const expiresAt = i.now + INVITE_TTL;
  const [change] = await db.batch([
    db.prepare(CREATE_SQL).bind(await sha256Hex(token), i.repoId, i.role, expiresAt, i.actor.identity, i.actor.owner ? 1 : 0),
    auditAfterChange(db, {
      at: i.now,
      actor: i.actor.identity,
      action: 'invite.create',
      target: i.repoId ?? 'admin',
      detail: { role: i.role },
    }),
  ]);
  return change!.meta.changes > 0 ? { token, expiresAt } : null;
}

export type InviteState =
  | { status: 'ok'; repoId: string | null; role: InviteRole }
  | { status: 'invalid' | 'used' | 'expired' };

interface InviteRow {
  repo_id: string | null;
  role: InviteRole;
  expires_at: number;
  used_at: number | null;
}

async function findInvite(db: D1Database, token: string): Promise<{ hash: string; row: InviteRow | null }> {
  if (!isCapability(token)) return { hash: '', row: null };
  const hash = await sha256Hex(token);
  const row = await db
    .prepare('SELECT repo_id, role, expires_at, used_at FROM invites WHERE token_hash = ?')
    .bind(hash)
    .first<InviteRow>();
  return { hash, row };
}

function stateOf(row: InviteRow | null, now: number): InviteState {
  if (!row) return { status: 'invalid' };
  if (row.used_at !== null) return { status: 'used' };
  if (row.expires_at <= now) return { status: 'expired' };
  return { status: 'ok', repoId: row.repo_id, role: row.role };
}

export async function inviteState(db: D1Database, token: string, now: number): Promise<InviteState> {
  return stateOf((await findInvite(db, token)).row, now);
}

/**
 * 초대 수락. 소비(조건부 UPDATE)·역할 반영·변경 기록을 한 batch에 넣고, 뒤 두 SQL은 changes()로 앞 SQL의 성공에만 잇는다.
 * 수락은 역할을 낮추지 않는다(이미 담당자면 viewer 초대를 받아도 담당자로 남는다). 그래서 수락으로 마지막 담당자가 없어지지 않는다.
 */
export async function acceptInvite(db: D1Database, token: string, identity: string, now: number): Promise<InviteState> {
  const { hash, row } = await findInvite(db, token);
  if (!row) return { status: 'invalid' };
  const consume = db
    .prepare(
      `UPDATE invites SET used_by = ?2, used_at = ?3
       WHERE token_hash = ?1 AND used_at IS NULL AND used_by IS NULL AND expires_at > ?3`,
    )
    .bind(hash, identity, now);
  // role은 바뀌지 않는 열이라 어느 표에 넣을지는 앞서 읽은 값으로 고른다. 소비 성공 여부는 SQL이 다시 본다.
  const apply =
    row.role === 'admin'
      ? db
          .prepare(
            `INSERT INTO site_roles (identity, role, added_by, added_at)
             SELECT ?2, 'admin', created_by, ?3 FROM invites WHERE token_hash = ?1 AND role = 'admin' AND changes() > 0
             ON CONFLICT (identity) DO UPDATE SET role = 'admin'`,
          )
          .bind(hash, identity, now)
      : db
          .prepare(
            `INSERT INTO repo_members (repo_id, identity, role, added_by, added_at)
             SELECT repo_id, ?2, role, created_by, ?3 FROM invites
             WHERE token_hash = ?1 AND role IN ('manager', 'viewer') AND changes() > 0
             ON CONFLICT (repo_id, identity) DO UPDATE SET
               role = CASE WHEN repo_members.role = 'manager' THEN 'manager' ELSE excluded.role END`,
          )
          .bind(hash, identity, now);
  const [consumed] = await db.batch([
    consume,
    apply,
    auditAfterChange(db, { at: now, actor: identity, action: 'invite.accept', target: row.repo_id ?? 'admin', detail: { role: row.role } }),
  ]);
  if (consumed!.meta.changes > 0) return { status: 'ok', repoId: row.repo_id, role: row.role };
  const state = await inviteState(db, token, now);
  return state.status === 'ok' ? { status: 'used' } : state;
}

/** 관리 화면을 열 때 만료된 초대를 지운다(Cron을 따로 두지 않는다). */
export async function purgeExpiredInvites(db: D1Database, now: number): Promise<void> {
  await db.prepare('DELETE FROM invites WHERE expires_at <= ?').bind(now).run();
}

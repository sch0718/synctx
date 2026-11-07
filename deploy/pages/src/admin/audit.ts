/**
 * 바로 앞 SQL이 실제로 바꾼 행이 있을 때만 남기는 변경 기록. 같은 batch(한 트랜잭션)에서 바로 뒤에 둔다.
 * changes()는 같은 연결에서 마지막으로 끝난 INSERT·UPDATE·DELETE의 변경 건수다.
 */
export function auditAfterChange(
  db: D1Database,
  e: { at: number; actor: string; action: string; target: string; detail?: unknown },
): D1PreparedStatement {
  return db
    .prepare('INSERT INTO audit_log (at, actor, action, target, detail) SELECT ?, ?, ?, ?, ? WHERE changes() > 0')
    .bind(e.at, e.actor, e.action, e.target, e.detail === undefined ? null : JSON.stringify(e.detail));
}

export interface AuditRow {
  at: number;
  actor: string;
  action: string;
  target: string;
  detail: string | null;
}

export async function listAudit(db: D1Database, limit = 200): Promise<AuditRow[]> {
  const { results } = await db
    .prepare('SELECT at, actor, action, target, detail FROM audit_log ORDER BY at DESC, rowid DESC LIMIT ?')
    .bind(limit)
    .all<AuditRow>();
  return results;
}

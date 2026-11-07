-- synctx 정적 Viewer 로그인 모드의 역할·초대·변경 기록(설계 3.3절). 시각은 epoch ms다.
-- identity: Google은 소문자 이메일, 카카오·네이버는 'kakao:<sub>'·'naver:<sub>'.
-- repo_id는 static-export.yaml의 저장소 id다.

CREATE TABLE site_roles (
  identity TEXT PRIMARY KEY,
  role TEXT NOT NULL CHECK (role IN ('admin')),
  added_by TEXT NOT NULL,
  added_at INTEGER NOT NULL
);

CREATE TABLE repo_members (
  repo_id TEXT NOT NULL,
  identity TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('manager', 'viewer')),
  added_by TEXT NOT NULL,
  added_at INTEGER NOT NULL,
  PRIMARY KEY (repo_id, identity)
);
CREATE INDEX repo_members_identity ON repo_members(identity);

-- 토큰 원문은 초대 링크에만 있다. 여기에는 SHA-256 hex만 둔다. 관리자 초대는 repo_id가 NULL이다.
CREATE TABLE invites (
  token_hash TEXT PRIMARY KEY,
  repo_id TEXT,
  role TEXT NOT NULL CHECK (role IN ('admin', 'manager', 'viewer')),
  expires_at INTEGER NOT NULL,
  created_by TEXT NOT NULL,
  used_by TEXT,
  used_at INTEGER,
  CHECK ((repo_id IS NULL) = (role = 'admin'))
);

CREATE TABLE audit_log (
  at INTEGER NOT NULL,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  target TEXT NOT NULL,
  detail TEXT
);
CREATE INDEX audit_log_at ON audit_log(at);

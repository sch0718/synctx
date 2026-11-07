/** identity: Google은 소문자 이메일, 카카오·네이버는 'kakao:<sub>'·'naver:<sub>'(sub는 대소문자 유지). */
export function normalizeIdentity(raw: string): string {
  const s = raw.trim();
  const m = /^(kakao|naver):(.+)$/i.exec(s);
  return m ? `${m[1]!.toLowerCase()}:${m[2]}` : s.toLowerCase();
}

/** 직접 입력한 값이 identity 형식인지. 이메일 또는 kakao:·naver: 접두어. */
export function isIdentity(raw: string): boolean {
  const s = normalizeIdentity(raw);
  if (s.length > 320 || /[\s,]/.test(s)) return false;
  return /^(kakao|naver):[A-Za-z0-9_.-]+$/.test(s) || /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(s);
}

import type { DeviceSessionRow } from '@sch0718/firebase-session/worker';
import type { RepoInfo } from '../admin/repos';
import { escapeHtml } from '../http';
import { logoutForm, page } from './layout';

export interface SettingsInput {
  identity: string;
  repos: RepoInfo[];
  admin: boolean;
  sessions: DeviceSessionRow[];
}

const when = new Intl.DateTimeFormat('ko-KR', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'Asia/Seoul' });

export function providerLabel(identity: string): string {
  if (identity.startsWith('kakao:')) return '카카오';
  if (identity.startsWith('naver:')) return '네이버';
  return 'Google';
}

function revokeForm(fields: string, label: string): string {
  return `<form method="post" action="/auth/sessions/revoke">${fields}<button type="submit" class="btn">${label}</button></form>`;
}

function device(s: DeviceSessionRow): string {
  const action = s.current
    ? '<span class="device-current">이 기기</span>'
    : revokeForm(`<input type="hidden" name="id" value="${escapeHtml(s.id)}">`, '로그아웃시키기');
  return (
    `<li><span class="device-name">${escapeHtml(s.label)}</span>` +
    `<span class="device-meta">마지막 사용 ${escapeHtml(when.format(s.lastUsedAt))}</span>` +
    `<div class="device-action">${action}</div></li>`
  );
}

/** GET /settings. 계정·볼 수 있는 저장소·로그인한 기기·로그아웃. */
export function settingsPage(input: SettingsInput): string {
  const repos = input.repos.length
    ? '<ul class="auth-list">' +
      input.repos.map((r) => `<li><a href="/view/${encodeURIComponent(r.id)}/">${escapeHtml(r.name)}</a></li>`).join('') +
      '</ul>'
    : '<p>아직 볼 수 있는 저장소가 없습니다. 관리자에게 위 계정 값을 알려 주세요.</p>';
  const others = input.sessions.filter((s) => !s.current).length;
  return page(
    '설정',
    '<h1 class="page-title">설정</h1>' +
      '<section class="auth-section" aria-labelledby="settings-account"><h2 id="settings-account">계정</h2>' +
      `<p>${providerLabel(input.identity)}로 로그인했습니다.</p>` +
      `<p><code class="identity">${escapeHtml(input.identity)}</code></p>` +
      '<p class="auth-muted">권한을 받을 때 관리자에게 이 값을 알려 주세요.</p></section>' +
      `<section class="auth-section" aria-labelledby="settings-repos"><h2 id="settings-repos">볼 수 있는 저장소</h2>${repos}</section>` +
      (input.admin
        ? '<section class="auth-section" aria-labelledby="settings-admin"><h2 id="settings-admin">관리</h2>' +
          '<p class="auth-muted">저장소와 열람자를 관리합니다.</p><p><a class="btn" href="/admin">관리 화면 열기</a></p></section>'
        : '') +
      '<section class="auth-section" aria-labelledby="settings-devices"><h2 id="settings-devices">로그인한 기기</h2>' +
      `<ul class="auth-list settings-devices">${input.sessions.map(device).join('')}</ul>` +
      (others > 1
        ? `<div class="auth-actions">${revokeForm('<input type="hidden" name="target" value="others">', '다른 기기 모두 로그아웃')}</div>`
        : '') +
      '</section>' +
      '<section class="auth-section" aria-labelledby="settings-logout"><h2 id="settings-logout">로그아웃</h2>' +
      `<p class="auth-muted">이 기기에서 로그아웃합니다.</p><div class="auth-actions">${logoutForm()}</div></section>` +
      '<p><a href="/view/">문서 첫 화면으로</a></p>',
    { account: true },
  );
}

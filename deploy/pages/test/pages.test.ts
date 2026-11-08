import type { DeviceSessionRow } from '@sch0718/firebase-session/worker';
import { describe, expect, it } from 'vitest';
import { deniedPage, errorPage, forbiddenPage } from '../src/pages/denied';
import { ACCOUNT_LINK, page } from '../src/pages/layout';
import { callbackPage, loginPage, unavailablePage } from '../src/pages/login';
import { settingsPage, type SettingsInput } from '../src/pages/settings';

const firebase = { apiKey: 'key', authDomain: 'example.firebaseapp.com', projectId: 'example', appId: 'app' };
const current: DeviceSessionRow = {
  id: 'current', label: '내 노트북', createdAt: 1_790_000_000_000,
  lastUsedAt: 1_790_000_060_000, expiresAt: 1_790_086_400_000, current: true,
};
const other: DeviceSessionRow = { ...current, id: '"><x', label: '<휴대폰> & "태블릿"', current: false };
const settings: SettingsInput = { identity: 'a@example.com', repos: [], admin: false, sessions: [current, other] };

function main(html: string): string {
  return html.match(/<main\b[^>]*>([\s\S]*?)<\/main>/)?.[1] ?? '';
}

describe('인증 화면 공통 틀', () => {
  it('테마 부트를 CSS보다 먼저 읽고 Viewer 머리글과 일곱 테마를 제공한다', () => {
    const html = page('t', '<p>x</p>');
    const boot = html.indexOf('<script src="/view/static/theme-boot.js"></script>');
    const variables = html.indexOf('href="/view/static/theme-vars.css"');
    const viewer = html.indexOf('href="/view/static/viewer.css"');
    expect(boot).toBeGreaterThan(-1);
    expect(boot).toBeLessThan(variables);
    expect(variables).toBeLessThan(viewer);
    expect(html).toContain('<html lang="ko" data-theme="github-light">');
    expect(html).toContain('data-action="theme" aria-label="테마"');
    expect(html.match(/<option\b/g)).toHaveLength(7);
    expect(html).not.toMatch(/<option\b[^>]*\bselected\b/);
    expect(html).toContain('<script src="/view/static/static-mode.js"></script>');
    expect(html).not.toContain(ACCOUNT_LINK);
  });

  it('계정 버튼은 켰을 때 머리글 마지막에 한 번만 넣는다', () => {
    const html = page('설정', '', { account: true });
    expect(html).toContain(`${ACCOUNT_LINK}</header>`);
    expect(html.split(ACCOUNT_LINK)).toHaveLength(2);
  });

  it('관리 화면용 wide 옵션으로 본문 폭을 넓힌다', () => {
    const html = page('관리', '<div id="admin-root"></div>', { account: true, wide: true });
    expect(html).toContain('class="auth-main auth-wide"');
    expect(html).toContain(`${ACCOUNT_LINK}</header>`);
  });

  it('테마 선택을 숨겨도 부트와 모듈 스크립트 및 추가 head를 유지한다', () => {
    const html = page('<제목>', '<p>본문</p>', {
      themeSelect: false, head: '<meta name="referrer" content="no-referrer">', scripts: ['/a?x="&'],
    });
    expect(html).not.toContain('data-action="theme"');
    expect(html).toContain('/view/static/theme-boot.js');
    expect(html).toContain('<title>&lt;제목&gt; - synctx</title>');
    expect(html).toContain('<meta name="referrer" content="no-referrer">');
    expect(html).toContain('<script type="module" src="/a?x=&quot;&amp;"></script>');
    expect(html.indexOf('/view/static/static-mode.js')).toBeLessThan(html.indexOf('type="module"'));
  });
});

describe('로그인과 안내 화면', () => {
  it('로그인은 계정 버튼 없이 테마 선택과 공급자 버튼 및 상태 줄을 제공한다', () => {
    const html = loginPage({ firebase, next: '/view/' });
    expect(html).not.toContain(ACCOUNT_LINK);
    expect(html).toContain('data-action="theme"');
    expect(html).toContain('data-login="google">Google로 로그인</button>');
    expect(html).toContain('data-login="kakao" disabled');
    expect(html).toContain('data-login="naver" disabled');
    expect(html).toContain('id="synctx-login-config"');
    expect(html).toContain('/view/static/synctx-auth/login.js');
  });

  it('설정된 소셜 공급자 버튼은 활성화한다', () => {
    const html = loginPage({ firebase, next: '/view/', kakaoClientId: 'kakao', naverClientId: 'naver' });
    expect(html).toContain('data-login="kakao">카카오로 로그인</button>');
    expect(html).toContain('data-login="naver">네이버로 로그인</button>');
  });

  it('콜백은 테마 선택과 계정 버튼을 숨기고 no-referrer와 로그인 처리를 유지한다', () => {
    const html = callbackPage();
    expect(html).not.toContain('data-action="theme"');
    expect(html).not.toContain(ACCOUNT_LINK);
    expect(html).toContain('/view/static/theme-boot.js');
    expect(html).toContain('<meta name="referrer" content="no-referrer">');
    expect(html).toContain('"mode":"callback"');
    expect(html).toContain('/view/static/synctx-auth/login.js');
  });

  it('거절 화면에는 계정 버튼과 로그아웃 동작을 보이고 계정을 이스케이프한다', () => {
    const html = deniedPage('<account>&');
    expect(html).toContain(`${ACCOUNT_LINK}</header>`);
    expect(html).toContain('<code class="identity">&lt;account&gt;&amp;</code>');
    expect(html).toContain('action="/auth/logout"');
  });

  it.each([
    ['주소 거절', forbiddenPage(), '볼 수 없는 주소입니다'],
    ['오류', errorPage(), '잠시 뒤 다시 시도해 주세요.'],
    ['이용 불가', unavailablePage('설정을 확인해 주세요.'), '설정을 확인해 주세요.'],
  ])('%s 안내에는 공통 머리글과 안내를 보이고 계정 버튼을 숨긴다', (_name, html, message) => {
    expect(html).toContain('class="shell-header"');
    expect(html).toContain(message);
    expect(html).not.toContain(ACCOUNT_LINK);
  });
});

describe('설정 화면', () => {
  it('계정 버튼과 identity 및 로그아웃 폼을 제공한다', () => {
    const html = settingsPage(settings);
    const body = main(html);
    expect(html).toContain(`${ACCOUNT_LINK}</header>`);
    expect(body).toContain('<code class="identity">a@example.com</code>');
    expect(body).toContain('action="/auth/logout"');
  });

  it('기기 목록은 현재 기기 표식과 이스케이프된 철회 폼을 보인다', () => {
    const body = main(settingsPage(settings));
    expect(body).toContain('<span class="device-name">내 노트북</span>');
    expect(body).toContain('<div class="device-action"><span class="device-current">이 기기</span></div>');
    expect(body).toContain('<span class="device-name">&lt;휴대폰&gt; &amp; &quot;태블릿&quot;</span>');
    expect(body).toContain('name="id" value="&quot;&gt;&lt;x"');
    expect(body).not.toContain('name="id" value="current"');
    expect(body.match(/action="\/auth\/sessions\/revoke"/g)).toHaveLength(1);
    expect(body).not.toContain('다른 기기 모두 로그아웃');
  });

  it.each([0, 1, 2])('다른 기기 %i대일 때 일괄 로그아웃은 두 대 이상에만 보인다', (count) => {
    const sessions = [current, ...Array.from({ length: count }, (_, i) => ({ ...other, id: `other-${i}` }))];
    const body = main(settingsPage({ ...settings, sessions }));
    if (count > 1) {
      expect(body).toContain('<div class="auth-actions"><form method="post" action="/auth/sessions/revoke"><input type="hidden" name="target" value="others">');
      expect(body).toContain('다른 기기 모두 로그아웃');
    } else {
      expect(body).not.toContain('name="target" value="others"');
    }
  });

  it('관리자에게 다섯 섹션과 관리 버튼을 보이고 저장소 이름 및 계정을 이스케이프한다', () => {
    const body = main(settingsPage({
      ...settings, identity: 'kakao:<id>&', admin: true, repos: [{ id: 'repo/a', name: '<A> & B' }],
    }));
    expect(body.match(/<section class="auth-section"/g)).toHaveLength(5);
    expect(body).toContain('<section class="auth-section" aria-labelledby="settings-admin">');
    expect(body).toContain('<a class="btn" href="/admin">관리 화면 열기</a>');
    expect(body).toContain('<ul class="auth-list"><li><a href="/view/repo%2Fa/">&lt;A&gt; &amp; B</a></li></ul>');
    expect(body).toContain('<code class="identity">kakao:&lt;id&gt;&amp;</code>');
    expect(body.indexOf('id="settings-account"')).toBeLessThan(body.indexOf('id="settings-repos"'));
    expect(body.indexOf('id="settings-repos"')).toBeLessThan(body.indexOf('id="settings-admin"'));
    expect(body.indexOf('id="settings-admin"')).toBeLessThan(body.indexOf('id="settings-devices"'));
    expect(body.indexOf('id="settings-devices"')).toBeLessThan(body.indexOf('id="settings-logout"'));
  });
});

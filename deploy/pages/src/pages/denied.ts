import { escapeHtml } from '../http';
import { logoutForm, page } from './layout';

/** 권한 없음 화면. 저장소가 있는지 없는지는 알려 주지 않는다. */
export function deniedPage(identity: string): string {
  return page(
    '볼 수 없는 문서',
    '<h1 class="page-title">볼 수 없는 문서입니다</h1>' +
      '<p>이 주소를 볼 권한이 없습니다. 볼 수 있어야 한다면 관리자에게 이 값을 알려 주세요.</p>' +
      `<p><code class="identity">${escapeHtml(identity)}</code></p>` +
      '<p><a href="/view/">첫 화면으로</a></p>' +
      logoutForm(),
  );
}

export function forbiddenPage(): string {
  return page('볼 수 없는 주소', '<h1 class="page-title">볼 수 없는 주소입니다</h1><p><a href="/view/">첫 화면으로</a></p>');
}

export function errorPage(): string {
  return page(
    '잠시 이용할 수 없음',
    '<h1 class="page-title">잠시 이용할 수 없습니다</h1><p>잠시 뒤 다시 시도해 주세요.</p>',
  );
}

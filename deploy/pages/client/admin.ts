// 관리 화면(/admin). 서버가 만든 틀 안에 표와 입력란을 그린다. 값은 textContent로만 넣는다.
interface Repo { id: string; name: string }
interface Me { identity: string; owner: boolean; admin: boolean; repos: Repo[] }
interface Member { identity: string; role: 'manager' | 'viewer'; added_by: string; added_at: number }
interface AdminRow { identity: string; added_by: string; added_at: number }
interface AuditRow { at: number; actor: string; action: string; target: string; detail: string | null }

const ROLE = { manager: '담당자', viewer: '열람자' } as const;
const ERRORS: Record<string, string> = {
  forbidden: '권한이 없습니다.',
  'last-manager': '마지막 담당자는 지우거나 열람자로 바꿀 수 없습니다.',
  'not-found': '찾지 못했습니다.',
  'bad-identity': 'identity 형식이 아닙니다. 이메일 또는 kakao:<값>·naver:<값>으로 적어 주세요.',
  'login-required': '로그인이 필요합니다. 새로고침해 주세요.',
};

async function api<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api/admin${path}`, {
    method,
    credentials: 'same-origin',
    headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
    body: body === undefined ? null : JSON.stringify(body),
  });
  const data = (await res.json().catch(() => ({}))) as { error?: string };
  if (!res.ok) throw new Error(ERRORS[data.error ?? ''] ?? `요청이 실패했습니다(${res.status}).`);
  return data as T;
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, props: Record<string, unknown> = {}, ...children: (Node | string)[]): HTMLElementTagNameMap[K] {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
}

const when = (ms: number) => new Date(ms).toLocaleString('ko-KR');

function statusLine(): HTMLParagraphElement {
  return el('p', { className: 'admin-status', role: 'status' });
}

async function run(out: HTMLElement, task: () => Promise<void>) {
  out.textContent = '';
  try {
    await task();
  } catch (e) {
    out.textContent = e instanceof Error ? e.message : String(e);
  }
}

function inviteBox(label: string, create: () => Promise<{ url: string; expiresAt: number }>): HTMLElement {
  const out = statusLine();
  const button = el('button', { type: 'button', className: 'btn', textContent: label });
  button.addEventListener('click', () =>
    run(out, async () => {
      const { url, expiresAt } = await create();
      const input = el('input', { type: 'text', readOnly: true, value: url, size: 60 });
      const copy = el('button', { type: 'button', className: 'btn', textContent: '복사' });
      copy.addEventListener('click', () => void navigator.clipboard.writeText(url).then(() => (copy.textContent = '복사됨')));
      out.replaceChildren(input, copy, ` 1회용, ${when(expiresAt)}까지`);
    }),
  );
  return el('div', { className: 'admin-invite' }, button, out);
}

function identityForm(onSubmit: (identity: string, role?: 'manager' | 'viewer') => Promise<void>, withRole: boolean) {
  const out = statusLine();
  const input = el('input', { type: 'text', placeholder: '이메일 또는 kakao:… / naver:…', required: true, size: 32 });
  const role = el('select', {}, el('option', { value: 'viewer', textContent: '열람자' }), el('option', { value: 'manager', textContent: '담당자' }));
  const children: Node[] = [input, ...(withRole ? [role] : []), el('button', { type: 'submit', className: 'btn btn-primary', textContent: '추가' }), out];
  const form = el('form', { className: 'admin-form' }, ...children);
  form.addEventListener('submit', (ev) => {
    ev.preventDefault();
    void run(out, () => onSubmit(input.value, withRole ? (role.value as 'manager' | 'viewer') : undefined));
  });
  return form;
}

async function repoSection(repo: Repo): Promise<HTMLElement> {
  const section = el('section', { className: 'admin-repo' }, el('h2', { textContent: `${repo.name} (${repo.id})` }));
  const body = el('div');
  const base = `/repos/${encodeURIComponent(repo.id)}`;
  async function refresh() {
    const { members } = await api<{ members: Member[] }>('GET', `${base}/members`);
    const out = statusLine();
    const rows = members.map((m) => {
      const change = el('button', { type: 'button', className: 'btn', textContent: m.role === 'manager' ? '열람자로' : '담당자로' });
      change.addEventListener('click', () =>
        run(out, async () => {
          await api('POST', `${base}/members`, { identity: m.identity, role: m.role === 'manager' ? 'viewer' : 'manager' });
          await refresh();
        }),
      );
      const remove = el('button', { type: 'button', className: 'btn', textContent: '삭제' });
      remove.addEventListener('click', () =>
        run(out, async () => {
          if (!confirm(`${m.identity}을(를) 지울까요?`)) return;
          await api('DELETE', `${base}/members`, { identity: m.identity });
          await refresh();
        }),
      );
      return el('tr', {}, el('td', { textContent: m.identity }), el('td', { textContent: ROLE[m.role] }), el('td', { textContent: `${m.added_by} · ${when(m.added_at)}` }), el('td', {}, change, remove));
    });
    const table = members.length
      ? el('table', { className: 'admin-table' }, el('thead', {}, el('tr', {}, ...['identity', '역할', '추가', ''].map((t) => el('th', { textContent: t })))), el('tbody', {}, ...rows))
      : el('p', { textContent: '아직 아무도 없습니다.' });
    body.replaceChildren(
      table,
      out,
      identityForm(async (identity, role) => {
        await api('POST', `${base}/members`, { identity, role });
        await refresh();
      }, true),
      inviteBox('열람자 초대 링크 만들기', () => api('POST', `${base}/invites`, { role: 'viewer' })),
      inviteBox('담당자 초대 링크 만들기', () => api('POST', `${base}/invites`, { role: 'manager' })),
    );
  }
  await run(body, refresh);
  section.append(body);
  return section;
}

async function adminsSection(): Promise<HTMLElement> {
  const section = el('section', { className: 'admin-admins', id: 'admins' }, el('h2', { textContent: '관리자' }));
  const body = el('div');
  async function refresh() {
    const { admins } = await api<{ admins: AdminRow[] }>('GET', '/admins');
    const out = statusLine();
    const list = el('ul', {}, ...admins.map((a) => {
      const remove = el('button', { type: 'button', className: 'btn', textContent: '해제' });
      remove.addEventListener('click', () => run(out, async () => {
        await api('DELETE', '/admins', { identity: a.identity });
        await refresh();
      }));
      return el('li', {}, `${a.identity} (${a.added_by} · ${when(a.added_at)}) `, remove);
    }));
    body.replaceChildren(
      admins.length ? list : el('p', { textContent: '지정된 관리자가 없습니다.' }),
      out,
      identityForm(async (identity) => {
        await api('POST', '/admins', { identity });
        await refresh();
      }, false),
      inviteBox('관리자 초대 링크 만들기', () => api('POST', '/admin-invites', {})),
    );
  }
  await run(body, refresh);
  section.append(body);
  return section;
}

async function auditSection(): Promise<HTMLElement> {
  const section = el('section', { className: 'admin-audit', id: 'audit' }, el('h2', { textContent: '변경 기록' }));
  const body = el('div');
  await run(body, async () => {
    const { entries } = await api<{ entries: AuditRow[] }>('GET', '/audit');
    body.replaceChildren(
      el('table', { className: 'admin-table' },
        el('thead', {}, el('tr', {}, ...['시각', '누가', '무엇을', '대상', '내용'].map((t) => el('th', { textContent: t })))),
        el('tbody', {}, ...entries.map((e) => el('tr', {}, ...[when(e.at), e.actor, e.action, e.target, e.detail ?? ''].map((t) => el('td', { textContent: t })))))),
    );
  });
  section.append(body);
  return section;
}

async function main() {
  const root = document.getElementById('admin-root');
  if (!root) return;
  await run(root, async () => {
    const me = await api<Me>('GET', '/me');
    const parts: HTMLElement[] = [];
    if (me.owner) parts.push(el('nav', {}, el('a', { href: '#admins', textContent: '관리자' }), ' · ', el('a', { href: '#audit', textContent: '변경 기록' })));
    parts.push(el('h2', { textContent: me.owner ? '모든 저장소' : '내가 담당하는 저장소' }));
    if (!me.repos.length) parts.push(el('p', { textContent: '담당하는 저장소가 없습니다.' }));
    for (const repo of me.repos) parts.push(await repoSection(repo));
    if (me.owner) parts.push(await adminsSection(), await auditSection());
    root.replaceChildren(...parts);
  });
}

void main();

export {};

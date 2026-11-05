import test from 'node:test';
import assert from 'node:assert/strict';
import '../../src/static_export/assets/static-mode.js';

const mode = globalThis.SynctxStaticMode;
const BASE = 'https://docs.example.test/view/r/docs/a.md/';

const TABLE = {
  'github-light': { css: '/view/static/themes/github-light.css', hljs: '/view/static/vendor/hljs/github.min.css', mermaid: 'default' },
  'github-dark': { css: '/view/static/themes/github-dark.css', hljs: '/view/static/vendor/hljs/github-dark.min.css', mermaid: 'dark' },
  hacker: { css: '/view/static/themes/hacker.css', hljs: '/view/static/vendor/hljs/monokai.min.css', mermaid: 'dark' },
  corporate: { css: '/view/static/themes/corporate.css', hljs: '/view/static/vendor/hljs/github.min.css', mermaid: 'default' },
};

test('트리 주소를 폴더별 JSON 파일 주소로 바꾼다', () => {
  assert.equal(mode.rewriteApiUrl('/view/api/repos/r/tree?path=docs', BASE), '/view/api/repos/r/tree/docs.json');
  assert.equal(mode.rewriteApiUrl('/view/api/repos/r/tree?path=docs%2Fsub&ref=main', BASE), '/view/api/repos/r/tree/docs/sub.json');
  assert.equal(mode.rewriteApiUrl('/view/api/repos/r/tree?path=', BASE), '/view/api/repos/r/tree.json');
  assert.equal(mode.rewriteApiUrl('/view/api/repos/r/tree', BASE), '/view/api/repos/r/tree.json');
});

test('한글·공백·#·?가 든 경로는 구간마다 인코딩한다', () => {
  const path = '회의/팀 문서/a#b?c';
  const raw = '/view/api/repos/r/tree?path=' + encodeURIComponent(path);
  assert.equal(
    mode.rewriteApiUrl(raw, BASE),
    '/view/api/repos/r/tree/' + path.split('/').map(encodeURIComponent).join('/') + '.json',
  );
});

test('저장소 id도 인코딩된 그대로 둔다', () => {
  assert.equal(mode.rewriteApiUrl('/view/api/repos/%ED%95%9C/tree?path=a', BASE), '/view/api/repos/%ED%95%9C/tree/a.json');
});

test('파일 목록 주소는 쿼리를 떼고 files.json으로 바꾼다', () => {
  assert.equal(mode.rewriteApiUrl('/view/api/repos/r/files', BASE), '/view/api/repos/r/files.json');
  assert.equal(mode.rewriteApiUrl('/view/api/repos/r/files?ref=main', BASE), '/view/api/repos/r/files.json');
});

test('대상이 아닌 주소와 다른 출처, 상위 경로는 바꾸지 않는다', () => {
  for (const raw of [
    '/view/api/repos/r/branches',
    '/view/api/search?q=a',
    '/view/api/repos/r/download?path=a',
    '/view/static/viewer.css',
    'https://other.example.test/view/api/repos/r/tree?path=a',
    '/view/api/repos/r/tree?path=a%2F..%2Fb',
    '/view/api/repos/r/tree?path=..',
    '/view/api/repos/r/tree/extra?path=a',
  ]) {
    assert.equal(mode.rewriteApiUrl(raw, BASE), null, raw);
  }
});

test('저장된 테마가 없거나 표 밖이면 기본값으로 연다', () => {
  assert.equal(mode.resolveTheme(null, TABLE, 'corporate'), 'corporate');
  assert.equal(mode.resolveTheme('', TABLE, 'corporate'), 'corporate');
  assert.equal(mode.resolveTheme('nope', TABLE, 'corporate'), 'corporate');
  assert.equal(mode.resolveTheme('__proto__', TABLE, 'corporate'), 'corporate');
  assert.equal(mode.resolveTheme('toString', TABLE, 'corporate'), 'corporate');
  assert.equal(mode.resolveTheme('hacker', TABLE, 'corporate'), 'hacker');
});

test('선택값을 CSS·hljs·Mermaid로 옮긴다', () => {
  assert.deepEqual(mode.themeEntry('github-dark', TABLE, 'corporate'), { name: 'github-dark', ...TABLE['github-dark'] });
  assert.deepEqual(mode.themeEntry('hacker', TABLE, 'corporate'), { name: 'hacker', ...TABLE.hacker });
  assert.equal(mode.themeEntry('???', TABLE, 'corporate').name, 'corporate');
});

function fakeWindow({ stored = null, config = { table: TABLE, defaultTheme: 'corporate' }, storageThrows = false } = {}) {
  const calls = { fetch: [], reload: 0, saved: {} };
  const attrs = {};
  const hrefs = {};
  const listeners = [];
  const selects = [{ value: 'github-light', getAttribute: (k) => (k === 'data-action' ? 'theme' : null) }];
  const win = {
    SYNCTX_STATIC: config,
    location: { href: BASE, reload() { calls.reload += 1; } },
    localStorage: {
      getItem: (k) => { if (storageThrows) throw new Error('blocked'); return k === 'synctx-theme' ? stored : null; },
      setItem: (k, v) => { if (storageThrows) throw new Error('blocked'); calls.saved[k] = v; },
    },
    fetch: (input, init) => { calls.fetch.push([input, init]); return Promise.resolve('ok'); },
    document: {
      documentElement: { setAttribute: (k, v) => { attrs[k] = v; } },
      getElementById: (id) => (id === 'theme-css' || id === 'hljs-css' ? { setAttribute: (k, v) => { hrefs[id] = v; } } : null),
      querySelectorAll: (sel) => (sel === 'select[data-action="theme"]' ? selects : []),
      addEventListener: (type, fn, capture) => listeners.push({ type, fn, capture }),
    },
  };
  return { win, calls, attrs, hrefs, listeners, selects };
}

test('설정이 없으면 아무것도 바꾸지 않는다', () => {
  const ctx = fakeWindow();
  delete ctx.win.SYNCTX_STATIC;
  const original = ctx.win.fetch;
  assert.equal(mode.init(ctx.win), false);
  assert.equal(ctx.win.fetch, original);
  assert.equal(ctx.listeners.length, 0);
});

test('init은 fetch를 바꾸고 저장된 테마를 반영한다', async () => {
  const ctx = fakeWindow({ stored: 'hacker' });
  assert.equal(mode.init(ctx.win), true);
  await ctx.win.fetch('/view/api/repos/r/tree?path=docs');
  await ctx.win.fetch('/view/api/repos/r/files?ref=main', { headers: { a: 'b' } });
  await ctx.win.fetch('/view/api/repos/r/branches');
  assert.deepEqual(ctx.calls.fetch.map((c) => c[0]), [
    '/view/api/repos/r/tree/docs.json',
    '/view/api/repos/r/files.json',
    '/view/api/repos/r/branches',
  ]);
  assert.equal(ctx.attrs['data-theme'], 'hacker');
  assert.equal(ctx.hrefs['theme-css'], TABLE.hacker.css);
  assert.equal(ctx.hrefs['hljs-css'], TABLE.hacker.hljs);
  assert.equal(ctx.selects[0].value, 'hacker');
});

test('저장소 접근이 막혀도 기본 테마로 동작한다', () => {
  const ctx = fakeWindow({ storageThrows: true });
  assert.equal(mode.init(ctx.win), true);
  assert.equal(ctx.attrs['data-theme'], 'corporate');
  assert.equal(ctx.selects[0].value, 'corporate');
});

test('테마 선택을 바꾸면 저장하고 재로드하며 Viewer의 쿼리 이동을 막는다', () => {
  const ctx = fakeWindow({ stored: null });
  mode.init(ctx.win);
  const listener = ctx.listeners.find((l) => l.type === 'change');
  assert.ok(listener && listener.capture === true);
  let stopped = false;
  const select = { value: 'github-dark', getAttribute: (k) => (k === 'data-action' ? 'theme' : null) };
  listener.fn({ target: select, stopImmediatePropagation: () => { stopped = true; } });
  assert.equal(ctx.calls.saved['synctx-theme'], 'github-dark');
  assert.equal(ctx.calls.reload, 1);
  assert.equal(stopped, true);
});

test('표 밖 값이나 다른 선택 상자는 저장하지 않는다', () => {
  const ctx = fakeWindow();
  mode.init(ctx.win);
  const listener = ctx.listeners.find((l) => l.type === 'change');
  const other = { value: 'x', getAttribute: (k) => (k === 'data-action' ? 'repo' : null) };
  let stopped = false;
  listener.fn({ target: other, stopImmediatePropagation: () => { stopped = true; } });
  assert.equal(stopped, false);
  const bad = { value: 'nope', getAttribute: (k) => (k === 'data-action' ? 'theme' : null) };
  listener.fn({ target: bad, stopImmediatePropagation: () => { stopped = true; } });
  assert.deepEqual(ctx.calls.saved, {});
  assert.equal(ctx.calls.reload, 0);
});

test('폴더 이동 주소의 path 쿼리를 읽어 트리를 펼친다', () => {
  const ctx = fakeWindow();
  ctx.win.location.href = 'https://docs.example.test/view/r?path=docs%2Fsub';
  const expanded = [];
  ctx.win.expandToPath = (p) => expanded.push(p);
  ctx.win.document.getElementById = (id) => (id === 'file-tree' ? { dataset: {} } : null);
  mode.init(ctx.win);
  assert.deepEqual(expanded, ['docs/sub']);
});

test('쿼리가 없거나 이미 펼침 경로가 있으면 펼치지 않는다', () => {
  const none = fakeWindow();
  const calls = [];
  none.win.expandToPath = (p) => calls.push(p);
  none.win.document.getElementById = (id) => (id === 'file-tree' ? { dataset: {} } : null);
  mode.init(none.win);
  const preset = fakeWindow();
  preset.win.location.href = 'https://docs.example.test/view/r?path=docs';
  preset.win.expandToPath = (p) => calls.push(p);
  preset.win.document.getElementById = (id) => (id === 'file-tree' ? { dataset: { expandPath: 'docs' } } : null);
  mode.init(preset.win);
  assert.deepEqual(calls, []);
});

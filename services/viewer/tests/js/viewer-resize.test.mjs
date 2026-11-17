import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const coreScript = readFileSync(new URL('../../src/static/viewer-core.js', import.meta.url), 'utf8');
const viewerScript = readFileSync(new URL('../../src/static/viewer.js', import.meta.url), 'utf8');

function target() {
  const listeners = new Map();
  const classes = new Set();
  const attrs = new Map();
  const captures = new Set();
  return {
    dataset: {},
    classList: { add: (v) => classes.add(v), remove: (v) => classes.delete(v), contains: (v) => classes.has(v) },
    setAttribute: (k, v) => attrs.set(k, String(v)),
    getAttribute: (k) => attrs.get(k),
    setPointerCapture: (id) => captures.add(id),
    hasPointerCapture: (id) => captures.has(id),
    releasePointerCapture: (id) => captures.delete(id),
    focus() {},
    addEventListener(type, fn) { listeners.set(type, [...(listeners.get(type) || []), fn]); },
    fire(type, props = {}) {
      const event = { pointerId: 1, button: 0, preventDefault() { this.defaultPrevented = true; }, ...props };
      for (const fn of listeners.get(type) || []) fn(event);
      return event;
    },
  };
}

function viewer({ stored = null, viewport = 1400, toc = false, blocked = false, handle = true } = {}) {
  const win = target();
  const resizer = target();
  const nav = target();
  nav.getBoundingClientRect = () => ({ left: 12 });
  const styles = new Map();
  const storage = new Map(stored === null ? [] : [['synctx-nav-width', stored]]);
  const access = () => { if (blocked) throw new Error('storage blocked'); };
  win.innerWidth = viewport;
  win.matchMedia = () => ({ matches: false, addEventListener() {} });
  win.localStorage = {
    getItem(k) { access(); return storage.get(k) ?? null; },
    setItem(k, v) { access(); storage.set(k, String(v)); },
    removeItem(k) { access(); storage.delete(k); },
  };
  const doc = {
    ...target(), body: target(),
    documentElement: { style: { setProperty: (k, v) => styles.set(k, v) } },
    querySelectorAll: () => [],
    querySelector: (sel) => ({ '#shell-nav': nav, '.nav-resizer': handle ? resizer : null,
      '[data-action="nav-resize"]': handle ? resizer : null, '.shell-toc': toc ? {} : null }[sel] || null),
  };
  const context = vm.createContext({ window: win, document: doc, VIEW_PREFIX: '/view', ACTIVE_REPO: '', ACTIVE_REF: '' });
  vm.runInContext(coreScript, context);
  win.SynctxViewerCore = context.SynctxViewerCore;
  vm.runInContext(viewerScript, context);
  return { win, doc, resizer, storage, width: () => styles.get('--shell-nav-w') };
}

test('손잡이가 없으면 너비를 적용하지 않는다', () => {
  assert.equal(viewer({ handle: false, stored: '500' }).width(), undefined);
});

test('창 크기와 목차에 맞추되 다시 넓히면 저장한 너비를 복원한다', () => {
  const ctx = viewer({ stored: '550', viewport: 1100, toc: true });
  assert.equal(ctx.width(), '480px');
  assert.equal(ctx.resizer.getAttribute('aria-valuemin'), '200');
  assert.equal(ctx.resizer.getAttribute('aria-valuemax'), '480');
  assert.equal(ctx.resizer.getAttribute('aria-valuenow'), '480');
  ctx.win.innerWidth = 1099;
  ctx.win.fire('resize');
  assert.equal(ctx.width(), '550px');
  ctx.win.innerWidth = 900;
  ctx.win.fire('resize');
  assert.equal(ctx.width(), '500px');
  ctx.win.innerWidth = 1400;
  ctx.win.fire('resize');
  assert.equal(ctx.width(), '550px');
  assert.equal(ctx.storage.get('synctx-nav-width'), '550');
});

test('키보드로 16px·64px씩 조절하고 Home·End 범위를 저장한다', () => {
  const ctx = viewer({ viewport: 900 });
  for (const [key, shiftKey, expected] of [['ArrowRight', false, '296'], ['ArrowLeft', true, '232'],
    ['Home', false, '200'], ['ArrowLeft', false, '200'], ['End', false, '500']]) {
    assert.equal(ctx.resizer.fire('keydown', { key, shiftKey }).defaultPrevented, true);
    assert.equal(ctx.width(), expected + 'px');
    assert.equal(ctx.storage.get('synctx-nav-width'), expected);
    assert.equal(ctx.resizer.getAttribute('aria-valuenow'), expected);
  }
  assert.equal(ctx.resizer.fire('keydown', { key: 'Tab' }).defaultPrevented, undefined);
});

test('더블클릭하면 기본 너비로 돌아가고 저장값을 지운다', () => {
  const ctx = viewer({ stored: '450' });
  ctx.resizer.fire('dblclick');
  assert.equal(ctx.width(), '280px');
  assert.equal(ctx.storage.has('synctx-nav-width'), false);
});

for (const end of ['pointerup', 'pointercancel', 'lostpointercapture']) {
  test(`${end}에서 드래그 상태를 해제하고 너비를 저장한다`, () => {
    const ctx = viewer();
    ctx.resizer.fire('pointerdown', { button: 2 });
    ctx.resizer.fire('pointermove', { clientX: 512 });
    assert.equal(ctx.width(), '280px');
    ctx.resizer.fire('pointerdown');
    assert.equal(ctx.resizer.hasPointerCapture(1), true);
    assert.equal(ctx.doc.body.classList.contains('nav-resizing'), true);
    ctx.resizer.fire('pointermove', { clientX: 512, pointerId: 2 });
    assert.equal(ctx.width(), '280px');
    ctx.resizer.fire('pointermove', { clientX: 512 });
    assert.equal(ctx.width(), '500px');
    assert.equal(ctx.storage.has('synctx-nav-width'), false);
    ctx.resizer.fire(end);
    assert.equal(ctx.doc.body.classList.contains('nav-resizing'), false);
    assert.equal(ctx.resizer.classList.contains('is-dragging'), false);
    assert.equal(ctx.storage.get('synctx-nav-width'), '500');
    ctx.resizer.fire('pointermove', { clientX: 612 });
    assert.equal(ctx.width(), '500px');
  });
}

test('저장소 접근이 차단되어도 조절과 초기화가 동작한다', () => {
  const ctx = viewer({ blocked: true });
  assert.equal(ctx.width(), '280px');
  ctx.resizer.fire('keydown', { key: 'ArrowRight' });
  assert.equal(ctx.width(), '296px');
  ctx.resizer.fire('dblclick');
  assert.equal(ctx.width(), '280px');
});

test('움직이지 않은 클릭은 저장된 너비를 바꾸지 않는다', () => {
  const ctx = viewer({ stored: '550', viewport: 1100, toc: true });
  ctx.resizer.fire('pointerdown');
  ctx.resizer.fire('pointerup');
  assert.equal(ctx.storage.get('synctx-nav-width'), '550');
});

test('수정 키를 누른 방향키는 가로채지 않는다', () => {
  const ctx = viewer();
  for (const mod of ['altKey', 'ctrlKey', 'metaKey']) {
    assert.equal(ctx.resizer.fire('keydown', { key: 'ArrowLeft', [mod]: true }).defaultPrevented, undefined);
  }
  assert.equal(ctx.width(), '280px');
});

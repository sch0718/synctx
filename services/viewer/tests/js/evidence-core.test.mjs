import test from 'node:test';
import assert from 'node:assert/strict';
import '../../src/static/meeting/evidence-core.js';
const core = globalThis.SynctxEvidenceCore;

test('말풍선은 링크 아래, 넘치면 위로', () => {
  const vp = { width: 1000, height: 800 };
  assert.deepEqual(core.popupPosition({ left: 100, top: 100, bottom: 120, width: 50 }, { width: 300, height: 200 }, vp),
    { left: 100, top: 126, flipped: false });
  assert.deepEqual(core.popupPosition({ left: 100, top: 700, bottom: 720, width: 50 }, { width: 300, height: 200 }, vp),
    { left: 100, top: 494, flipped: true });
  assert.equal(core.popupPosition({ left: 900, top: 10, bottom: 30, width: 50 }, { width: 300, height: 50 }, vp).left, 692);
  assert.equal(core.popupPosition({ left: 10, top: 10, bottom: 30, width: 50 }, { width: 400, height: 50 }, { width: 375, height: 800 }).left, 8);
});

test('새 창·새 탭으로 여는 클릭', () => {
  const e = (x) => ({ button: 0, ctrlKey: false, metaKey: false, shiftKey: false, altKey: false, ...x });
  assert.equal(core.opensInNewContext(e()), false);
  for (const k of [{ button: 1 }, { ctrlKey: true }, { metaKey: true }, { shiftKey: true }, { altKey: true }]) {
    assert.equal(core.opensInNewContext(e(k)), true);
  }
  assert.equal(core.opensInNewContext(e(), '_blank'), true);
  assert.equal(core.opensInNewContext(e(), '_self'), false);
  assert.equal(core.opensInNewContext(e(), ''), false);
});

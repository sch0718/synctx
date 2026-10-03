import test from 'node:test';
import assert from 'node:assert/strict';
import '../../src/static/meeting/memo-core.js';

const core = globalThis.SynctxMemoCore;
const BACKSLASH = String.fromCharCode(92);
const LS = String.fromCharCode(0x2028);
const PS = String.fromCharCode(0x2029);

export function memo(over = {}) {
  return {
    id: 'm_abcdef1234',
    kind: 'note',
    anchor: null,
    text: '메모',
    owner: '',
    due: '',
    created_at: '2026-10-08T05:00:00.000Z',
    updated_at: '2026-10-08T05:00:00.000Z',
    ...over,
  };
}

test('LIMITS와 상수', () => {
  assert.deepEqual(core.LIMITS, { text: 10000, owner: 200, quote: 1000 });
  assert.equal(core.SCHEMA, 1);
  assert.deepEqual(core.KINDS, ['note', 'decision', 'action']);
});

test('newId 형식', () => {
  const id = core.newId();
  assert.match(id, /^m_[A-Za-z0-9]{10}$/);
  assert.notEqual(core.newId(), id);
});

test('encodeForScript: 위험 문자가 남지 않고 원래 값으로 돌아온다', () => {
  const value = { t: '</script><script>alert(1)</script> <!-- --> &amp; ' + LS + PS };
  const encoded = core.encodeForScript(value);
  for (const ch of ['<', '>', '&', LS, PS]) assert.equal(encoded.includes(ch), false, ch);
  assert.ok(encoded.includes(BACKSLASH + 'u003c'));
  assert.ok(encoded.includes(BACKSLASH + 'u2028'));
  assert.deepEqual(JSON.parse(encoded), value);
});

test('makeAnchor: 1,000자 이하는 전체, 초과는 앞 1,000자', () => {
  const text = 'a'.repeat(2000);
  assert.equal(core.makeAnchor(text, 0, 1000).quote.length, 1000);
  const long = core.makeAnchor(text, 10, 1011);
  assert.equal(long.quote.length, 1000);
  assert.equal(long.end - long.start, 1001);
});

test('locate: 정확히 일치', () => {
  const text = '가나다 백업 결과를 보고 라마바';
  const a = core.makeAnchor(text, 4, 13);
  assert.deepEqual(core.locate(text, a), { start: 4, end: 13, lost: false });
});

test('locate: 앞에 글이 추가되면 인용문으로 다시 찾는다', () => {
  const before = '가나다 백업 결과를 보고 라마바';
  const a = core.makeAnchor(before, 4, 13);
  const after = '추가된 문장. ' + before;
  const r = core.locate(after, a);
  assert.equal(after.slice(r.start, r.end), '백업 결과를 보고');
  assert.equal(r.lost, false);
});

test('locate: 인용문이 여러 번 나오면 원래 위치에 가장 가까운 것', () => {
  const text = 'XX 보고 YYYYYYYYYY 보고 ZZ';
  const second = text.lastIndexOf('보고');
  const a = { start: second + 3, end: second + 5, quote: '보고' };
  assert.equal(core.locate(text, a).start, second);
});

test('locate: 인용문이 사라지면 lost', () => {
  const r = core.locate('완전히 다른 본문', { start: 0, end: 4, quote: '백업 결과' });
  assert.equal(r.lost, true);
});

test('locate: anchor가 null이면 null', () => {
  assert.equal(core.locate('본문', null), null);
});

test('locate: 1,000자보다 긴 선택은 같은 길이로 복원', () => {
  const text = 'p'.repeat(50) + 'q'.repeat(1500) + 'r'.repeat(50);
  const a = core.makeAnchor(text, 50, 1550);
  const shifted = 'N'.repeat(7) + text;
  const r = core.locate(shifted, a);
  assert.deepEqual(r, { start: 57, end: 1557, lost: false });
});

test('locate ① 분기: 앞부분은 같고 뒷부분만 삭제되면 end를 본문 길이로 자른다', () => {
  const original = 's'.repeat(1500);
  const a = core.makeAnchor(original, 0, 1500);
  const shortened = 's'.repeat(1100);
  assert.deepEqual(core.locate(shortened, a), { start: 0, end: 1100, lost: false });
});

test('locate ② 분기: 다시 찾은 범위가 본문 끝을 넘으면 자른다', () => {
  const a = { start: 0, end: 30, quote: '끝부분' };
  const text = '앞에 무언가 끝부분';
  const r = core.locate(text, a);
  assert.equal(r.start, text.indexOf('끝부분'));
  assert.equal(r.end, text.length);
});

test('summarize: 종류별 묶음과 문서 순서, 위치 없는 메모는 끝', () => {
  const memos = [
    memo({ id: 'm_aaaaaa', kind: 'decision', anchor: { start: 50, end: 60, quote: 'x' } }),
    memo({ id: 'm_bbbbbb', kind: 'decision', anchor: { start: 10, end: 20, quote: 'y' } }),
    memo({ id: 'm_cccccc', kind: 'action', anchor: null, owner: '', due: '' }),
    memo({ id: 'm_dddddd', kind: 'note', anchor: { start: 5, end: 6, quote: 'z' } }),
    memo({ id: 'm_eeeeee', kind: 'note', anchor: null }),
    memo({ id: 'm_ffffff', kind: 'note', anchor: null, deleted_at: '2026-10-08T06:00:00.000Z' }),
  ];
  const s = core.summarize(memos, { m_aaaaaa: 50, m_bbbbbb: 10, m_dddddd: 5 });
  assert.deepEqual(s.decisions.map(m => m.id), ['m_bbbbbb', 'm_aaaaaa']);
  assert.deepEqual(s.actions.map(m => m.id), ['m_cccccc']);
  assert.deepEqual(s.notes.map(m => m.id), ['m_dddddd']);
  assert.deepEqual(s.general.map(m => m.id), ['m_eeeeee']);
});

test('stripRuntime: _로 시작하는 키 제외', () => {
  const m = core.stripRuntime({ ...memo(), _lost: true, _loc: {} });
  assert.equal('_lost' in m, false);
  assert.equal('_loc' in m, false);
  assert.equal(m.id, 'm_abcdef1234');
});

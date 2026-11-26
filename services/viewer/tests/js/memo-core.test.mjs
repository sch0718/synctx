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

const DOC = { repo_id: 'test-repo', meeting_id: 'dx-21', path: 'docs/m.md', title: 'DX 21번 검토' };

function sampleMemos() {
  return [
    memo({ id: 'm_dec0001', kind: 'decision', text: '월 1회 보고로 확정', anchor: { start: 10, end: 20, quote: '백업 결과 보고' } }),
    memo({ id: 'm_act0001', kind: 'action', text: '양식 | 초안\n작성', owner: '홍길동', due: '2026-10-15', anchor: { start: 30, end: 40, quote: '양식' } }),
    memo({ id: 'm_not0001', kind: 'note', text: '참고 -->  주석 깨기 시도', anchor: { start: 5, end: 8, quote: '참고' } }),
    memo({ id: 'm_gen0001', kind: 'note', text: '전체 의견', anchor: null }),
    memo({ id: 'm_del0001', kind: 'note', text: '지운 메모', deleted_at: '2026-10-08T06:00:00.000Z', updated_at: '2026-10-08T06:00:00.000Z' }),
  ];
}

test('exportPayload: 삭제 표시 제외, 런타임 키 제외', () => {
  const memos = sampleMemos();
  memos[0]._lost = true;
  const p = core.exportPayload({ doc: DOC, memos }, '2026-10-08T07:00:00.000Z');
  assert.equal(p.schema, 1);
  assert.deepEqual(p.doc, DOC);
  assert.equal(p.exported_at, '2026-10-08T07:00:00.000Z');
  assert.equal(p.memos.some(m => m.id === 'm_del0001'), false);
  assert.equal(p.memos.some(m => '_lost' in m), false);
});

test('toMarkdown: 사람이 읽는 부분과 데이터 주석', () => {
  const md = core.toMarkdown({ doc: DOC, memos: sampleMemos() }, {
    exported_at: '2026-10-08T07:00:00.000Z', starts_at: '2026-10-08T14:00:00+09:00',
    positions: { m_dec0001: 10, m_act0001: 30, m_not0001: 5 },
  });
  assert.ok(md.startsWith('# DX 21번 검토 — 회의 메모\n'));
  assert.ok(md.includes('- 회의 일시: 2026-10-08T14:00:00+09:00'));
  assert.ok(md.includes('## 결정사항\n\n1. 월 1회 보고로 확정 — 인용: "백업 결과 보고"'));
  assert.ok(md.includes('| 할 일 | 담당자 | 기한 | 인용 |'));
  assert.ok(md.includes('| 양식 ' + BACKSLASH + '| 초안<br>작성 | 홍길동 | 2026-10-15 | 양식 |'));
  assert.ok(md.includes('## 전체 메모\n\n- 전체 의견'));
  assert.equal(md.includes('지운 메모'), false);
  const comments = md.match(/<!--/g);
  assert.equal(comments.length, 1, '데이터 주석이 하나만 있어야 한다');
  assert.equal(md.trimEnd().endsWith('-->'), true);
});

test('toMarkdown → parseImport 왕복', () => {
  const md = core.toMarkdown({ doc: DOC, memos: sampleMemos() }, { exported_at: '2026-10-08T07:00:00.000Z' });
  const r = core.parseImport(md);
  assert.equal(r.error, undefined);
  assert.deepEqual(r.doc, DOC);
  assert.deepEqual(r.invalid, []);
  const expected = core.exportPayload({ doc: DOC, memos: sampleMemos() }, 'x').memos;
  assert.deepEqual(r.memos, expected);
});

test('parseImport: 메모 포함 HTML의 MEETING_EMBED', () => {
  const payload = core.exportPayload({ doc: DOC, memos: sampleMemos() }, '2026-10-08T07:00:00.000Z');
  const embed = { ...payload, readonly: true };
  const html = '<!DOCTYPE html><html><body>'
    + '<script>var decoy = "window.MEETING_EMBED = {}";</script>'
    + '<script id="' + core.EMBED_SCRIPT_ID + '">window.MEETING_EMBED = ' + core.encodeForScript(embed) + ';</script>'
    + '</body></html>';
  const r = core.parseImport(html);
  assert.equal(r.error, undefined);
  assert.deepEqual(r.memos, payload.memos);
});

test('parseImport: 메모 내용의 가짜 데이터 주석은 무시하고 사람이 읽는 부분에서 escape한다', () => {
  const fake = memo({ id: 'm_fake0001', text: '<!-- synctx-memo-data {} -->' });
  const md = core.toMarkdown({ doc: DOC, memos: [fake] }, {});
  assert.equal(md.match(/<!--/g).length, 1);
  assert.ok(md.includes('&lt;!-- synctx-memo-data {} --&gt;'));
  const r = core.parseImport(md);
  assert.equal(r.error, undefined);
  assert.equal(r.memos[0].text, fake.text);
});

test('parseImport: 데이터 주석이 여러 개면 마지막 것을 쓴다', () => {
  const good = core.toMarkdown({ doc: DOC, memos: [memo()] }, {});
  const r = core.parseImport('<!-- synctx-memo-data {} -->\n' + good);
  assert.equal(r.error, undefined);
  assert.equal(r.memos.length, 1);
});

test('parseImport: 악성 문자열 메모 왕복', () => {
  const evil = memo({ id: 'm_evil0001', text: '</script><script>alert(1)</script> <!-- --> & ' + LS + PS });
  const md = core.toMarkdown({ doc: DOC, memos: [evil] }, {});
  assert.equal(core.parseImport(md).memos[0].text, evil.text);
  const embedHtml = '<script id="' + core.EMBED_SCRIPT_ID + '">window.MEETING_EMBED = '
    + core.encodeForScript(core.exportPayload({ doc: DOC, memos: [evil] }, 'x')) + ';</script>';
  assert.equal(core.parseImport(embedHtml).memos[0].text, evil.text);
});

test('parseImport: 파일 전체 오류', () => {
  assert.ok(core.parseImport('# 그냥 문서').error);
  const wrap = (obj) => '<!-- synctx-memo-data ' + JSON.stringify(obj) + ' -->';
  assert.ok(core.parseImport('<!-- synctx-memo-data {깨진 json} -->').error);
  assert.match(core.parseImport(wrap({ schema: 2, doc: DOC, memos: [] })).error, /schema/);
  assert.match(core.parseImport(wrap({ schema: 1, doc: DOC, memos: {} })).error, /memos/);
  assert.match(core.parseImport(wrap({ schema: 1, doc: { repo_id: 'r' }, memos: [] })).error, /doc/);
});

test('parseImport: 메모 단위 오류는 invalid로 빠지고 나머지는 가져온다', () => {
  const good = memo();
  const bad = [
    ['id', memo({ id: 'bad' })],
    ['kind', memo({ id: 'm_kind0001', kind: 'todo' })],
    ['text', memo({ id: 'm_text0001', text: 'x'.repeat(10001) })],
    ['owner', memo({ id: 'm_ownr0001', owner: 'o'.repeat(201) })],
    ['due', memo({ id: 'm_duee0001', due: '10/15' })],
    ['created_at', memo({ id: 'm_crea0001', created_at: 'yesterday' })],
    ['updated_at', memo({ id: 'm_updt0001', updated_at: '2026-10-08' })],
    ['deleted_at', memo({ id: 'm_dele0001', deleted_at: 'never' })],
    ['anchor', memo({ id: 'm_anch0001', anchor: { start: 5, end: 2, quote: 'x' } })],
    ['anchor.quote', memo({ id: 'm_quot0001', anchor: { start: 0, end: 1001, quote: 'q'.repeat(1001) } })],
  ];
  const text = '<!-- synctx-memo-data ' + core.encodeForScript({ schema: 1, doc: DOC, memos: [good, ...bad.map(b => b[1])] }) + ' -->';
  const r = core.parseImport(text);
  assert.deepEqual(r.memos.map(m => m.id), [good.id]);
  assert.deepEqual(r.invalid.map(i => i.reason), bad.map(b => b[0]));
});

test('길이 제한 경계값 왕복', () => {
  const text = 'x'.repeat(2000);
  const memos = [
    memo({ id: 'm_lim00001', text: 't'.repeat(10000) }),
    memo({ id: 'm_lim00002', kind: 'action', owner: 'o'.repeat(200) }),
    memo({ id: 'm_lim00003', anchor: core.makeAnchor(text, 0, 1000) }),
    memo({ id: 'm_lim00004', anchor: core.makeAnchor(text, 0, 1001) }),
  ];
  const r = core.parseImport(core.toMarkdown({ doc: DOC, memos }, {}));
  assert.deepEqual(r.invalid, []);
  assert.equal(r.memos.length, 4);
  assert.equal(r.memos[3].anchor.quote.length, 1000);
  assert.equal(r.memos[3].anchor.end, 1001);
});

test('merge: 추가·최신 우선·건너뜀', () => {
  const cur = [memo({ id: 'm_same0001', text: '현재', updated_at: '2026-10-08T05:00:00.000Z' })];
  const inc = [
    memo({ id: 'm_same0001', text: '파일(최신)', updated_at: '2026-10-08T06:00:00.000Z' }),
    memo({ id: 'm_new00001', text: '새 메모' }),
  ];
  const r = core.merge(cur, inc);
  assert.deepEqual(r.added.map(m => m.id), ['m_new00001']);
  assert.deepEqual(r.updated.map(m => m.id), ['m_same0001']);
  assert.equal(r.memos.find(m => m.id === 'm_same0001').text, '파일(최신)');

  const r2 = core.merge(cur, [memo({ id: 'm_same0001', text: '파일(오래됨)', updated_at: '2026-10-08T04:00:00.000Z' })]);
  assert.deepEqual(r2.skipped.map(m => m.id), ['m_same0001']);
  assert.equal(r2.memos[0].text, '현재');
});

test('merge: 삭제 표시가 더 최신이면 삭제 유지하고 deletedConflicts에 넣는다', () => {
  const cur = [memo({ id: 'm_del00001', deleted_at: '2026-10-08T06:00:00.000Z', updated_at: '2026-10-08T06:00:00.000Z' })];
  const inc = [memo({ id: 'm_del00001', text: '파일', updated_at: '2026-10-08T05:00:00.000Z' })];
  const r = core.merge(cur, inc);
  assert.deepEqual(r.deletedConflicts.map(m => m.id), ['m_del00001']);
  assert.ok(r.memos[0].deleted_at);
  assert.deepEqual(r.restored, []);
});

test('merge: restoreDeleted면 되살리고 updated_at을 지금 시각으로', () => {
  const cur = [memo({ id: 'm_del00001', deleted_at: '2026-10-08T06:00:00.000Z', updated_at: '2026-10-08T06:00:00.000Z' })];
  const inc = [memo({ id: 'm_del00001', text: '파일', updated_at: '2026-10-08T05:00:00.000Z' })];
  const r = core.merge(cur, inc, { restoreDeleted: true, now: '2026-10-09T00:00:00.000Z' });
  assert.deepEqual(r.restored.map(m => m.id), ['m_del00001']);
  assert.equal('deleted_at' in r.memos[0], false);
  assert.equal(r.memos[0].text, '파일');
  assert.equal(r.memos[0].updated_at, '2026-10-09T00:00:00.000Z');
});

test('merge: 삭제 이후에 편집된 메모(파일 쪽이 더 최신)는 되살린다', () => {
  const cur = [memo({ id: 'm_del00001', deleted_at: '2026-10-08T06:00:00.000Z', updated_at: '2026-10-08T06:00:00.000Z' })];
  const inc = [memo({ id: 'm_del00001', text: '삭제 후 편집', updated_at: '2026-10-08T07:00:00.000Z' })];
  const r = core.merge(cur, inc);
  assert.deepEqual(r.updated.map(m => m.id), ['m_del00001']);
  assert.deepEqual(r.deletedConflicts, []);
  assert.equal(r.memos[0].deleted_at, undefined);
});

function elapsedMs(fn) {
  const t0 = performance.now();
  const result = fn();
  return { result, ms: performance.now() - t0 };
}

test('parseImport: 닫는 주석이 없는 긴 공백 입력도 선형 시간에 끝난다', () => {
  const { result, ms } = elapsedMs(() => core.parseImport('<!-- synctx-' + 'memo-data ' + ' '.repeat(50000) + 'x'));
  assert.ok(result.error);
  assert.ok(ms < 200, 'took ' + ms + 'ms');
});

test('parseImport: 닫는 script가 없는 긴 공백 임베드도 선형 시간에 끝난다', () => {
  const { result, ms } = elapsedMs(() => core.parseImport('<script id="' + core.EMBED_SCRIPT_ID + '">window.MEETING_EMBED = ' + ' '.repeat(50000)));
  assert.ok(result.error);
  assert.ok(ms < 200, 'took ' + ms + 'ms');
});

test('toMarkdown: 회의 일시 줄도 주석 구분자를 escape하고 왕복된다', () => {
  const md = core.toMarkdown({ doc: DOC, memos: [memo()] }, { starts_at: 'x --> y <!-- z' });
  assert.equal(md.split('<!--').length - 1, 1);
  assert.ok(md.includes('- 회의 일시: x --&gt; y &lt;!-- z'));
  assert.deepEqual(core.parseImport(md).memos.map(m => m.id), ['m_abcdef1234']);
});

test('parseImport: 여는 embed 태그가 매우 많고 닫는 태그가 하나뿐이어도 선형 시간에 끝난다', () => {
  const open = '<script id="' + core.EMBED_SCRIPT_ID + '">';
  const { result, ms } = elapsedMs(() => core.parseImport(open.repeat(30000) + '</' + 'script>'));
  assert.ok(result.error);
  assert.ok(ms < 200, 'took ' + ms + 'ms');
});

test('parseImport: 태그마다 window.MEETING_EMBED가 붙어 있어도(대입 없음) 선형 시간에 끝난다', () => {
  const open = '<script id="' + core.EMBED_SCRIPT_ID + '">window.MEETING_EMBED ';
  const { result, ms } = elapsedMs(() => core.parseImport(open.repeat(30000) + '</' + 'script>'));
  assert.ok(result.error);
  assert.ok(ms < 200, 'took ' + ms + 'ms');
});

function sharedStore() {
  return new Map();
}

function tab(map) {
  const counter = { writes: 0 };
  const storage = {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => { map.set(k, String(v)); counter.writes += 1; },
    removeItem: (k) => { map.delete(k); },
    key: (i) => Array.from(map.keys())[i] ?? null,
    get length() { return map.size; },
  };
  return { storage, counter, lastWritten: {}, display: {} };
}

const PREFIX = 'synctx-meeting:test-repo:dx-21:v1:memo:';
const key = (id) => PREFIX + id;

function apply(t, id, r) {
  if (r.removed) { delete t.display[id]; delete t.lastWritten[id]; return; }
  t.display[id] = r.display;
  t.lastWritten[id] = r.lastWritten;
}

function save(t, m) {
  const r = core.writeMemo(t.storage, key(m.id), m, t.lastWritten[m.id] || null);
  apply(t, m.id, r);
  return r;
}

function receive(t, id) {
  const r = core.reconcileMemo(t.storage, key(id), t.lastWritten[id] || null);
  apply(t, id, r);
  return r;
}

test('저장 배치: 두 탭이 같은 상태를 읽은 뒤 서로 다른 메모를 저장해도 둘 다 남는다', () => {
  const map = sharedStore();
  const A = tab(map);
  const B = tab(map);
  assert.deepEqual(core.listMemos(A.storage, PREFIX), []);
  assert.deepEqual(core.listMemos(B.storage, PREFIX), []);
  save(A, memo({ id: 'm_tabA0001', text: 'A' }));
  save(B, memo({ id: 'm_tabB0001', text: 'B' }));
  assert.deepEqual(core.listMemos(A.storage, PREFIX).map(m => m.id).sort(), ['m_tabA0001', 'm_tabB0001']);
});

test('같은 메모: 저장값이 더 최신이면 오래된 쪽은 쓰지 않고 저장값을 보여준다', () => {
  const map = sharedStore();
  const A = tab(map);
  const B = tab(map);
  save(A, memo({ id: 'm_same0001', text: '새 값', updated_at: '2026-10-08T06:00:00.000Z' }));
  const r = save(B, memo({ id: 'm_same0001', text: '오래된 값', updated_at: '2026-10-08T05:00:00.000Z' }));
  assert.equal(r.written, false);
  assert.equal(B.counter.writes, 0);
  assert.equal(B.display.m_same0001.text, '새 값');
  assert.equal(core.readMemo(A.storage, key('m_same0001')).text, '새 값');
});

test('삭제 표시 후 오래된 탭의 수정 저장은 거부된다', () => {
  const map = sharedStore();
  const A = tab(map);
  const B = tab(map);
  save(A, memo({ id: 'm_del00001', deleted_at: '2026-10-08T06:00:00.000Z', updated_at: '2026-10-08T06:00:00.000Z' }));
  save(B, memo({ id: 'm_del00001', text: '오래된 수정', updated_at: '2026-10-08T05:30:00.000Z' }));
  assert.ok(core.readMemo(A.storage, key('m_del00001')).deleted_at);
});

test('검사와 쓰기가 교차해도 최신 값으로 수렴하고 오래된 쪽은 다시 쓰지 않는다', () => {
  const map = sharedStore();
  const A = tab(map);
  const B = tab(map);
  const fresh = memo({ id: 'm_race0001', text: '새 값', updated_at: '2026-10-08T06:00:00.000Z' });
  const stale = memo({ id: 'm_race0001', text: '오래된 값', updated_at: '2026-10-08T05:00:00.000Z' });
  // A 검사 → B 검사(둘 다 빈 저장소를 봄) → A 쓰기 → B 쓰기
  save(A, fresh);
  B.storage.setItem(key(stale.id), JSON.stringify(stale));
  B.lastWritten[stale.id] = stale;
  B.display[stale.id] = stale;
  const bWritesBefore = B.counter.writes;
  // 각 탭에 상대 쓰기의 storage 이벤트 전달
  const ra = receive(A, 'm_race0001');
  assert.equal(ra.rewrite, true);
  const rb = receive(B, 'm_race0001');
  assert.equal(rb.rewrite, false);
  assert.equal(core.readMemo(A.storage, key('m_race0001')).text, '새 값');
  assert.equal(A.display.m_race0001.text, '새 값');
  assert.equal(B.display.m_race0001.text, '새 값');
  assert.equal(B.counter.writes, bWritesBefore, 'B는 다시 쓰지 않는다');
});

test('바로잡기 전에 새 값 탭이 닫히면 저장값과 남은 탭 화면이 같다', () => {
  const map = sharedStore();
  const A = tab(map);
  const B = tab(map);
  save(A, memo({ id: 'm_race0002', text: '새 값', updated_at: '2026-10-08T06:00:00.000Z' }));
  const stale = memo({ id: 'm_race0002', text: '오래된 값', updated_at: '2026-10-08T05:00:00.000Z' });
  B.storage.setItem(key(stale.id), JSON.stringify(stale));
  B.lastWritten[stale.id] = stale;
  receive(B, 'm_race0002');
  assert.equal(B.display.m_race0002.text, core.readMemo(B.storage, key('m_race0002')).text);
});

test('바로잡기는 초안이 아니라 lastWritten을 다시 쓴다', () => {
  const map = sharedStore();
  const A = tab(map);
  const v1 = memo({ id: 'm_drft0001', text: 'v1', updated_at: '2026-10-08T06:00:00.000Z' });
  save(A, v1);
  const draft = { text: 'v2(저장 대기)' };
  const v0 = memo({ id: 'm_drft0001', text: 'v0', updated_at: '2026-10-08T05:00:00.000Z' });
  map.set(key(v0.id), JSON.stringify(v0));
  const r = receive(A, 'm_drft0001');
  assert.equal(r.rewrite, true);
  assert.equal(core.readMemo(A.storage, key('m_drft0001')).text, 'v1');
  assert.equal(draft.text, 'v2(저장 대기)');
  const v2 = { ...v1, text: draft.text, updated_at: '2026-10-08T06:01:00.000Z' };
  save(A, v2);
  assert.equal(core.readMemo(A.storage, key('m_drft0001')).text, 'v2(저장 대기)');
});

test('키가 지워지면 lastWritten과 화면에서 빼고 다시 만들지 않는다', () => {
  const map = sharedStore();
  const A = tab(map);
  save(A, memo({ id: 'm_gone0001' }));
  const writes = A.counter.writes;
  map.delete(key('m_gone0001'));
  const r = receive(A, 'm_gone0001');
  assert.equal(r.removed, true);
  assert.equal(A.counter.writes, writes);
  assert.equal(map.has(key('m_gone0001')), false);
  assert.equal('m_gone0001' in A.lastWritten, false);
  assert.equal('m_gone0001' in A.display, false);
});

test('쓰기 후 확인: 저장값이 다르면 저장값 기준으로 판단한다', () => {
  const map = sharedStore();
  const A = tab(map);
  const newer = memo({ id: 'm_post0001', text: '다른 탭(최신)', updated_at: '2026-10-08T07:00:00.000Z' });
  const original = A.storage.setItem;
  A.storage.setItem = (k, v) => { original(k, v); map.set(k, JSON.stringify(newer)); };
  const r = save(A, memo({ id: 'm_post0001', text: '내 값', updated_at: '2026-10-08T06:00:00.000Z' }));
  assert.equal(r.display.text, '다른 탭(최신)');
});

test('깨진 저장값은 lastWritten으로 바로잡는다', () => {
  const map = sharedStore();
  const A = tab(map);
  save(A, memo({ id: 'm_brkn0001', text: '정상' }));
  map.set(key('m_brkn0001'), '{깨짐');
  const r = receive(A, 'm_brkn0001');
  assert.equal(r.rewrite, true);
  assert.equal(core.readMemo(A.storage, key('m_brkn0001')).text, '정상');
});

test('purgeTombstones: 30일 지난 삭제 표시만 지운다', () => {
  const map = sharedStore();
  const A = tab(map);
  const now = Date.parse('2026-11-10T00:00:00.000Z');
  save(A, memo({ id: 'm_old00001', deleted_at: '2026-10-01T00:00:00.000Z', updated_at: '2026-10-01T00:00:00.000Z' }));
  save(A, memo({ id: 'm_new00001', deleted_at: '2026-11-01T00:00:00.000Z', updated_at: '2026-11-01T00:00:00.000Z' }));
  save(A, memo({ id: 'm_live0001' }));
  map.set('다른 키', 'x');
  const removed = core.purgeTombstones(A.storage, PREFIX, now);
  assert.deepEqual(removed, [key('m_old00001')]);
  assert.deepEqual(core.listMemos(A.storage, PREFIX).map(m => m.id).sort(), ['m_live0001', 'm_new00001']);
  assert.equal(core.TOMBSTONE_TTL_MS, 30 * 24 * 60 * 60 * 1000);
});

test('listMemos: 접두어가 다른 키와 깨진 값은 무시', () => {
  const map = sharedStore();
  const A = tab(map);
  save(A, memo({ id: 'm_list0001' }));
  map.set(PREFIX + 'm_broken01', '{');
  map.set('synctx-meeting:other:v1:memo:m_other001', JSON.stringify(memo({ id: 'm_other001' })));
  assert.deepEqual(core.listMemos(A.storage, PREFIX).map(m => m.id), ['m_list0001']);
});

test('nextUpdatedAt: 저장값이 미래 시각이어도 그보다 1ms 이상 늦은 시각을 만든다', () => {
  const future = new Date(Date.now() + 5 * 60 * 1000).toISOString();
  const stored = memo({ id: 'm_futr0001', text: '가져온 값', updated_at: future });
  const map = sharedStore();
  const A = tab(map);
  save(A, stored);
  const next = core.nextUpdatedAt(stored);
  assert.ok(Date.parse(next) > Date.parse(stored.updated_at));
  assert.ok(Date.parse(next) >= Date.now() - 1000);
  const r = save(A, memo({ id: 'm_futr0001', text: '내 수정', updated_at: next }));
  assert.equal(r.written, true);
  assert.equal(core.readMemo(A.storage, key('m_futr0001')).text, '내 수정');
  const del = save(A, memo({ id: 'm_futr0001', text: '내 수정', deleted_at: core.nowIso(), updated_at: core.nextUpdatedAt(r.display) }));
  assert.equal(del.written, true);
  assert.ok(core.readMemo(A.storage, key('m_futr0001')).deleted_at);
});

test('nextUpdatedAt: 과거 값이나 없는 값이면 지금 시각 이상', () => {
  const before = Date.now();
  const past = core.nextUpdatedAt(memo());
  assert.ok(Date.parse(past) >= before);
  assert.ok(Date.parse(core.nextUpdatedAt({})) >= before);
  assert.ok(Date.parse(core.nextUpdatedAt(null)) >= before);
  assert.ok(Date.parse(core.nextUpdatedAt({ updated_at: '깨짐' })) >= before);
});

test('importSummary: 모두 저장되면 건수만 알리고 오류가 아니다', () => {
  const res = { added: [memo()], updated: [], restored: [], skipped: [memo(), memo()] };
  const s = core.importSummary(res, 1, { failed: 0, stale: 0 });
  assert.equal(s.isError, false);
  assert.equal(s.text, '추가 1, 갱신 0, 복원 0, 건너뜀 2, 형식 오류로 제외 1');
});

test('importSummary: 저장 실패가 있으면 화면 전용 상태를 오류로 알린다', () => {
  const res = { added: [memo(), memo()], updated: [], restored: [], skipped: [] };
  const s = core.importSummary(res, 0, { failed: 2, stale: 0 });
  assert.equal(s.isError, true);
  assert.match(s.text, /^추가 2, 갱신 0, 복원 0, 건너뜀 0, 형식 오류로 제외 0\. /);
  assert.match(s.text, /저장하지 못한 메모 2건은 화면에만 남아 있어 새로고침하면 사라집니다/);
});

test('importSummary: 더 최신 내용 때문에 저장하지 않은 건수도 함께 알린다', () => {
  const res = { added: [], updated: [memo()], restored: [], skipped: [] };
  const s = core.importSummary(res, 0, { failed: 0, stale: 1 });
  assert.equal(s.isError, true);
  assert.match(s.text, /다른 탭의 더 최신 내용이 있어 저장하지 않은 메모 1건/);
});

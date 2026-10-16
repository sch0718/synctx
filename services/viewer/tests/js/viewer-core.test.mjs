import test from 'node:test';
import assert from 'node:assert/strict';
import '../../src/static/viewer-core.js';

const core = globalThis.SynctxViewerCore;
const PATHS = [
  'README.md',
  '회의/팀문서/2026-10-14_DX_팀문서_회의/DX_팀문서_회의_20261014.md',
  '회의/팀문서/2026-10-14_DX_팀문서_회의/DX_팀문서_회의_20261014_참석자용.md',
  'docs/guide.md',
  'docs/sub/guide-advanced.md',
  'src/"quoted" <b>.md',
];

test('빈 검색어는 결과가 없다', () => {
  assert.deepEqual(core.matchFiles(PATHS, '   '), []);
});

test('모든 단어가 들어 있는 경로만, 파일 이름 앞부분 일치가 먼저', () => {
  assert.deepEqual(core.matchFiles(PATHS, 'guide'), ['docs/guide.md', 'docs/sub/guide-advanced.md']);
  assert.deepEqual(core.matchFiles(PATHS, 'DX 참석자'), [PATHS[2]]);
});

test('대소문자와 한글을 구분 없이 찾고 개수를 자른다', () => {
  assert.deepEqual(core.matchFiles(PATHS, 'readme'), ['README.md']);
  assert.equal(core.matchFiles(PATHS, '.md', 2).length, 2);
});

test('특수 문자가 든 경로도 그대로 돌려준다', () => {
  assert.deepEqual(core.matchFiles(PATHS, '"quoted"'), ['src/"quoted" <b>.md']);
});

test('최근 문서는 중복을 지우고 앞에 넣고 8개로 자른다', () => {
  let list = [];
  for (let i = 0; i < 10; i++) list = core.addRecent(list, { repo: 'r', path: `d${i}.md`, title: `D${i}` }, i);
  assert.equal(list.length, core.RECENT_LIMIT);
  assert.equal(list[0].path, 'd9.md');
  list = core.addRecent(list, { repo: 'r', path: 'd5.md', title: 'D5' }, 99);
  assert.equal(list[0].path, 'd5.md');
  assert.equal(list.filter((x) => x.path === 'd5.md').length, 1);
  assert.equal(list[0].at, 99);
});

test('브랜치가 다르면 다른 항목으로 기억한다', () => {
  let list = core.addRecent([], { repo: 'r', path: 'a.md', ref: '', title: 'A' }, 1);
  list = core.addRecent(list, { repo: 'r', path: 'a.md', ref: 'feat/x', title: 'A' }, 2);
  assert.equal(list.length, 2);
  assert.equal(list[0].ref, 'feat/x');
  list = core.addRecent(list, { repo: 'r', path: 'a.md', ref: 'feat/x', title: 'A' }, 3);
  assert.equal(list.length, 2);
  assert.equal(core.addRecent([], { repo: 'r', path: 'b.md', title: 'B' }, 1)[0].ref, '');
});

test('제목이 없으면 경로를 쓰고 200자로 자른다', () => {
  const [entry] = core.addRecent([], { repo: 'r', path: 'a.md', title: '' }, 1);
  assert.equal(entry.title, 'a.md');
  const [long] = core.addRecent([], { repo: 'r', path: 'b.md', title: 'x'.repeat(300) }, 1);
  assert.equal(long.title.length, 200);
});

test('저장 값이 깨졌거나 배열이 아니면 빈 목록', () => {
  assert.deepEqual(core.parseRecent(null), []);
  assert.deepEqual(core.parseRecent('{bad'), []);
  assert.deepEqual(core.parseRecent('{"a":1}'), []);
  assert.deepEqual(core.parseRecent('[{"repo":"r","path":"a.md","title":"A","at":1}]').length, 1);
});

test('파일 목록 요청이 실패하면 빈 목록과 구분하고 다음 호출에서 다시 요청한다', async () => {
  let calls = 0;
  const loader = core.createFileLoader(() => {
    calls += 1;
    if (calls === 1) throw new Error('down');
    return ['a.md'];
  });
  assert.deepEqual(await loader.load(), { ok: false, files: null });
  assert.equal(loader.files(), null);
  assert.deepEqual(await loader.load(), { ok: true, files: ['a.md'] });
  await loader.load();
  assert.equal(calls, 2);
});

test('빈 저장소는 실패가 아니라 빈 목록이다', async () => {
  const loader = core.createFileLoader(async () => []);
  assert.deepEqual(await loader.load(), { ok: true, files: [] });
  assert.deepEqual(loader.files(), []);
});

test('동시에 불러도 요청은 한 번만 보낸다', async () => {
  let calls = 0;
  const loader = core.createFileLoader(async () => { calls += 1; return ['a.md']; });
  await Promise.all([loader.load(), loader.load()]);
  assert.equal(calls, 1);
});

test('브랜치 목록은 기본 브랜치에 (기본)을 붙이고 현재 브랜치를 고른다', () => {
  const choices = core.branchChoices({ branches: ['main', 'feat/x'], default: 'main' }, 'feat/x');
  assert.deepEqual(choices, [
    { value: 'main', label: 'main (기본)', selected: false },
    { value: 'feat/x', label: 'feat/x', selected: true },
  ]);
});

test('목록에 없는 현재 브랜치도 잃지 않는다', () => {
  const choices = core.branchChoices({ branches: ['main'], default: 'main' }, 'old');
  assert.deepEqual(choices.map((c) => c.value), ['old', 'main']);
  assert.equal(choices[0].selected, true);
});

test('형식이 맞지 않는 브랜치 응답은 무시한다', () => {
  assert.equal(core.branchChoices(null, 'main'), null);
  assert.equal(core.branchChoices({ branches: 'main' }, 'main'), null);
  assert.equal(core.branchChoices({ branches: [] , default: 'main'}, 'main'), null);
});

test('파일, 본문, 전체 결과 순서로 묶는다', () => {
  const data = { repos: [{ id: 'r', name: 'Repo R', status: 'ok', matches: [
    { path: 'a.md', line: 1, before: '', match: 'x', after: '' }, { path: 'b.md', line: 2, before: '', match: 'y', after: '' }] }] };
  const opts = core.finderOptions(['f1.md', 'f2.md'], data, 'ab');
  assert.deepEqual(opts.map((o) => o.kind), ['file', 'file', 'content', 'content', 'all']);
  assert.equal(opts[0].group, '파일');
  assert.equal(opts[2].group, '본문');
  assert.equal(opts[2].repo, 'r');
  assert.equal(opts[2].repoName, 'Repo R');
});

test('개수를 자르고 한 글자 검색어에는 전체 결과를 넣지 않는다', () => {
  const many = { repos: [{ id: 'r', status: 'ok', matches: Array.from({ length: 9 }, (_, i) => ({ path: `p${i}.md`, line: 1, before: '', match: 't', after: '' })) }] };
  const opts = core.finderOptions(Array.from({ length: 12 }, (_, i) => `f${i}.md`), many, 'ab');
  assert.equal(opts.filter((o) => o.kind === 'file').length, 8);
  assert.equal(opts.filter((o) => o.kind === 'content').length, 5);
  assert.equal(core.finderOptions(['f.md'], null, 'a').some((o) => o.kind === 'all'), false);
});

test('검색어 길이를 UTF-16 단위가 아니라 글자 수로 세고 100글자까지만 받는다', () => {
  assert.equal(core.isSearchable('\u{1F600}'), false);
  assert.equal(core.isSearchable('\u{1F600}\u{1F600}'), true);
  assert.equal(core.isSearchable('a'), false);
  assert.equal(core.isSearchable('a'.repeat(100)), true);
  assert.equal(core.isSearchable('a'.repeat(101)), false);
  assert.equal(core.isSearchable('\u{1F600}'.repeat(100)), true);
  assert.equal(core.isSearchable('  ab  '), true);
});

test('이모지 한 글자나 101글자 검색어에는 전체 결과를 넣지 않는다', () => {
  const has = (q) => core.finderOptions(['f.md'], null, q).some((o) => o.kind === 'all');
  assert.equal(has('\u{1F600}'), false);
  assert.equal(has('\u{1F600}\u{1F600}'), true);
  assert.equal(has('a'.repeat(100)), true);
  assert.equal(has('a'.repeat(101)), false);
});

test('본문 응답이 없거나 실패 상태면 본문 묶음이 없다', () => {
  const data = { repos: [{ id: 'r', status: 'timeout', matches: [] }] };
  assert.deepEqual(core.finderOptions([], data, 'ab').map((o) => o.kind), ['all']);
});

function fakeClock() {
  let now = 0; const timers = [];
  return {
    setTimer: (fn, ms) => { const t = { at: now + ms, fn }; timers.push(t); return t; },
    clearTimer: (t) => { const i = timers.indexOf(t); if (i >= 0) timers.splice(i, 1); },
    tick(ms) { now += ms; timers.filter((t) => t.at <= now).forEach((t) => { timers.splice(timers.indexOf(t), 1); t.fn(); }); },
  };
}

test('입력이 멈춘 뒤 마지막 검색어로 한 번만 요청한다', async () => {
  const clock = fakeClock(); const asked = [];
  const s = core.createSearchScheduler({ delay: 250, request: async (q) => { asked.push(q); return { query: q }; }, onResult() {}, ...clock });
  s.schedule('회의'); clock.tick(100); s.schedule('회의모'); clock.tick(249);
  assert.deepEqual(asked, []);
  clock.tick(1);
  assert.deepEqual(asked, ['회의모']);
});

test('예약 중 취소하면 요청하지 않는다', () => {
  const clock = fakeClock(); const asked = [];
  const s = core.createSearchScheduler({ delay: 250, request: async (q) => { asked.push(q); }, onResult() {}, ...clock });
  s.schedule('회의'); s.cancel(); clock.tick(500);
  assert.deepEqual(asked, []);
});

test('늦게 온 앞 응답은 버리고 앞 요청은 취소한다', async () => {
  const clock = fakeClock(); const resolvers = {}; const signals = {}; const got = [];
  const s = core.createSearchScheduler({
    delay: 250,
    request: (q, signal) => { signals[q] = signal; return new Promise((r) => { resolvers[q] = r; }); },
    onResult: (q, data) => got.push(q),
    ...clock,
  });
  s.schedule('회의'); clock.tick(250);
  s.schedule('회의모드'); clock.tick(250);
  assert.equal(signals['회의'].aborted, true);
  resolvers['회의모드']({}); await Promise.resolve();
  resolvers['회의']({}); await Promise.resolve();
  assert.deepEqual(got, ['회의모드']);
});

test('같은 검색어로 돌아와도 앞 요청의 응답은 버린다', async () => {
  const clock = fakeClock(); const pending = []; const got = [];
  const s = core.createSearchScheduler({
    delay: 250,
    request: (q) => new Promise((r) => pending.push({ q, r })),
    onResult: (q) => got.push(q),
    ...clock,
  });
  s.schedule('A'); clock.tick(250);
  s.schedule('B'); clock.tick(250);
  s.schedule('A'); clock.tick(250);
  pending[0].r({}); await Promise.resolve();
  assert.deepEqual(got, []);
  pending[2].r({}); await Promise.resolve();
  assert.deepEqual(got, ['A']);
});

async function withUnhandledCheck(fn) {
  const unhandled = [];
  const onUnhandled = (e) => unhandled.push(e);
  process.on('unhandledRejection', onUnhandled);
  try {
    await fn();
    await new Promise((r) => setImmediate(r));
    assert.deepEqual(unhandled, []);
  } finally {
    process.off('unhandledRejection', onUnhandled);
  }
}

test('지금 요청이 실패하면 넘기지 않고 처리되지 않은 거절도 남기지 않는다', () => withUnhandledCheck(async () => {
  const clock = fakeClock(); const got = [];
  const s = core.createSearchScheduler({
    delay: 250, request: () => Promise.reject(new Error('500')), onResult: (q) => got.push(q), ...clock,
  });
  s.schedule('fail'); clock.tick(250);
  await new Promise((r) => setImmediate(r));
  assert.deepEqual(got, []);
}));

test('중단으로 AbortError가 나도 넘기지 않고 처리되지 않은 거절도 남기지 않는다', () => withUnhandledCheck(async () => {
  const clock = fakeClock(); const got = []; let seen = null; let aborts = 0;
  const s = core.createSearchScheduler({
    delay: 250,
    request: (q, signal) => new Promise((resolve, reject) => {
      seen = signal;
      signal.addEventListener('abort', () => { aborts += 1; reject(new DOMException('aborted', 'AbortError')); });
    }),
    onResult: (q) => got.push(q),
    ...clock,
  });
  s.schedule('slow'); clock.tick(250);
  s.cancel();
  assert.equal(seen.aborted, true);
  assert.equal(aborts, 1);
  await new Promise((r) => setImmediate(r));
  assert.deepEqual(got, []);
}));

test('진행 중 요청을 취소한 뒤 도착한 응답은 넘기지 않는다', async () => {
  const clock = fakeClock(); let resolve; const got = [];
  const s = core.createSearchScheduler({
    delay: 250,
    request: () => new Promise((r) => { resolve = r; }),
    onResult: (q) => got.push(q),
    ...clock,
  });
  s.schedule('회의'); clock.tick(250);
  s.cancel();
  resolve({}); await Promise.resolve();
  assert.deepEqual(got, []);
});

test('onResult가 예외를 던져도 처리되지 않은 거절을 남기지 않는다', () => withUnhandledCheck(async () => {
  const clock = fakeClock(); let called = 0;
  const s = core.createSearchScheduler({
    delay: 250,
    request: async () => ({}),
    onResult: () => { called += 1; throw new Error('render'); },
    ...clock,
  });
  s.schedule('회의'); clock.tick(250);
  await new Promise((r) => setImmediate(r));
  assert.equal(called, 1);
}));

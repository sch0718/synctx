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

import { createRequire } from 'node:module';
import test from 'node:test';
import assert from 'node:assert/strict';
const require = createRequire(import.meta.url);
const core = require('../../src/static/meeting/jitsi-core.js');
const now = 1800000000000;
const doc = 'repo:회의.md';
const msg = { schema: 'synctx-follow/1', doc, anchor: '안건', ts: now };
const validate = (changes = {}, mod = true, previous = 0) => core.validateMessage(JSON.stringify({ ...msg, ...changes }), doc, mod, previous);

test('verified moderator messages tolerate clock skew and require increasing sender timestamps', () => {
  assert.deepEqual(validate(), msg);
  assert.equal(validate({}, false), null);
  assert.equal(validate({}, 'moderator'), null);
  assert.equal(validate({ schema: 'other' }), null);
  assert.equal(validate({ doc: 'repo:다른.md' }), null);
  for (const skew of [-300000, 300000]) {
    assert.ok(validate({ ts: now + skew }));
    assert.equal(validate({ ts: now + skew }, true, now + skew), null);
    assert.ok(validate({ ts: now + skew + 1 }, true, now + skew));
  }
  assert.equal(validate({ ts: 0 }), null);
  assert.equal(validate({ ts: '1800000000000' }), null);
  assert.equal(validate({}, true, now), null);
  assert.deepEqual(validate({ ts: now - 15000 }), { ...msg, ts: now - 15000 });
  assert.ok(validate({ ts: now + 2000 }));
});

test('messages have one bounded target and reject malformed or oversized data', () => {
  for (const raw of ['null', '[]', '{', '"string"', ' '.repeat(2049)]) {
    assert.equal(core.validateMessage(raw, doc, true), null);
  }
  for (const changes of [{ anchor: '' }, { anchor: '\u0000x' }, { anchor: 'x'.repeat(257) },
    { anchor: null }, { anchor: undefined }, { slide: 1 }, { extra: 'x' }]) {
    assert.equal(validate(changes), null);
  }
  for (const slide of [0, -1, 1.1, 10001, '1', null]) {
    assert.equal(validate({ anchor: undefined, slide }), null);
  }
  assert.deepEqual(validate({ anchor: undefined, slide: 2 }), { schema: msg.schema, doc, slide: 2, ts: now });
  assert.equal(core.validateMessage(JSON.stringify({ ...msg, doc: '가'.repeat(700) }), '가'.repeat(700), true), null);
});

test('anchor resolution uses exact existing ids and decodes hashes without selectors', () => {
  const ids = ['안건', 'a.b[x]', 'one'];
  assert.equal(core.resolveAnchor('안건', ids), '안건');
  assert.equal(core.resolveAnchor('#%EC%95%88%EA%B1%B4', ids), '안건');
  assert.equal(core.resolveAnchor('a.b[x]', ids), 'a.b[x]');
  assert.equal(core.resolveAnchor('%00', ['\u0000']), null);
  for (const anchor of ['other', '#%zz', '', null, '__proto__', 'a\u0000', 'x'.repeat(257)]) {
    assert.equal(core.resolveAnchor(anchor, ids), null);
  }
});

test('throttle permits first send, bounds rate, and rejects backward clocks', () => {
  assert.equal(core.shouldSend(null, now, 250), true);
  assert.equal(core.shouldSend(now, now + 249, 250), false);
  assert.equal(core.shouldSend(now, now + 250, 250), true);
  assert.equal(core.shouldSend(now, now - 1, 250), false);
  assert.equal(core.shouldSend(now, NaN, 250), false);
});

test('moderator role comes only from rooms participants with matching ids', () => {
  const info = { rooms: [{ isMainRoom: true, participants: [{ id: 'p', role: 'moderator' }, { id: 'a', role: 'participant' }] },
    { participants: [{ id: 'b', role: 'moderator' }] }] };
  assert.equal(core.moderatorFor('p', info), true);
  assert.equal(core.moderatorFor('b', info), true);
  for (const id of ['a', 'missing', '', '__proto__']) assert.equal(core.moderatorFor(id, info), false);
  for (const invalid of [undefined, null, [], {}, { rooms: null }, { rooms: [null, {}] },
    { rooms: [{ participants: [{ participantId: 'p', role: 'moderator' }, { id: 'p' }] }] }]) {
    assert.equal(core.moderatorFor('p', invalid), false);
  }
});

test('join availability has reasons at exact boundaries and when disabled or cancelled', () => {
  const cfg = { enabled: true, joinable_from: new Date(now).toISOString(), joinable_until: new Date(now + 1000).toISOString() };
  assert.equal(core.joinReason(cfg, now), '');
  assert.match(core.joinReason(cfg, now - 1), /15분/);
  assert.match(core.joinReason(cfg, now + 1000), /1시간/);
  assert.match(core.joinReason({ ...cfg, enabled: false }, now), /꺼져/);
  assert.match(core.joinReason({ ...cfg, cancelled: true }, now), /취소/);
  assert.match(core.joinReason({ ...cfg, joinable_until: 'bad' }, now), /확인/);
  assert.equal(core.joinReason({ ...cfg, reason: '기본 브랜치만' }, now), '기본 브랜치만');
});

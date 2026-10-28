import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const core = require('../../src/static/meeting/jitsi-core.js');
const source = readFileSync(new URL('../../src/static/meeting/jitsi.js', import.meta.url), 'utf8');

let activeElement;
class Element {
  constructor(tag) { this.tagName = tag; this.attrs = {}; this.events = {}; this.children = []; this.hidden = false; }
  setAttribute(key, value) { this.attrs[key] = value; }
  getAttribute(key) { return this.attrs[key]; }
  addEventListener(key, fn) { this.events[key] = fn; }
  append(...children) { this.children.push(...children); }
  appendChild(child) { this.append(child); if (child.tagName === 'script') child.onload(); }
  querySelector(tag) { return this.children.find(child => child.tagName === tag); }
  focus() { this.focused = true; activeElement = this; }
  contains(el) { return el === this || this.children.some(child => child.contains(el)); }
  remove() {}
  click() { return this.events.click({ currentTarget: this }); }
}

function setup({ moderator = false, presenting = false, pending = false } = {}) {
  let now = 1800000000000;
  activeElement = null;
  class Clock extends Date { static now() { return now; } }
  const button = new Element('button'), anchor = new Element('h2');
  anchor.id = '안건';
  anchor.getBoundingClientRect = () => ({ top: 100 });
  anchor.scrollIntoView = () => { anchor.scrolled = (anchor.scrolled || 0) + 1; };
  const cfg = { enabled: true, public_url: 'https://meet.synctx.local:8443', doc: 'r:m.md', endpoint: '/api/jitsi',
    joinable_from: new Date(now - 1000).toISOString(), joinable_until: new Date(now + 600000).toISOString() };
  const article = { querySelectorAll: () => [anchor], contains: el => el === anchor };
  const body = new Element('body');
  body.classList = { contains: value => presenting && value === 'page-present' };
  const progress = new Element('progress');
  progress.setAttribute('aria-valuenow', '2');
  const documentEvents = {}, windowEvents = {}, timers = new Map();
  let timerId = 0, instance, tokenResolve;
  const document = {
    body, head: new Element('head'),
    get activeElement() { return activeElement; },
    getElementById: id => id === 'jitsi-config' ? { textContent: JSON.stringify(cfg) } : id === anchor.id ? anchor : null,
    querySelector: selector => selector === 'article.markdown-body' ? article : selector === '.present-progress' ? progress : null,
    querySelectorAll: selector => selector === '[data-jitsi-join]' ? [button] : [],
    createElement: tag => new Element(tag),
    addEventListener: (key, fn) => { documentEvents[key] = fn; },
    dispatchEvent: event => { documentEvents[event.type]?.(event); document.lastEvent = event; },
  };
  class Jitsi {
    constructor(domain, options) {
      this.domain = domain; this.options = options; this.events = {}; this.sent = [];
      this.iframe = new Element('iframe');
      this.iframe.setAttribute('allow', 'camera; microphone; display-capture; fullscreen; autoplay; clipboard-write');
      options.parentNode.appendChild(this.iframe);
      this.rooms = { rooms: [{ isMainRoom: true, participants: [{ id: 'p', role: 'moderator' }, { id: 'a', role: 'participant' }] }] };
      this.roomCalls = 0;
      instance = this;
    }
    getIFrame() { return this.iframe; }
    getParticipantsInfo() { return [{ participantId: 'p', displayName: 'Presenter' }, { participantId: 'a', displayName: 'Attendee' }]; }
    async getRoomsInfo() { this.roomCalls += 1; if (this.roomError) throw new Error('rooms'); return this.rooms; }
    localRoleChanged(role) { this.events.participantRoleChanged?.({ id: 'local', role }); }
    addListener(key, fn) { this.events[key] = fn; }
    executeCommand(...args) { this.sent.push(args); }
    dispose() { this.disposed = true; }
  }
  const token = { public_url: cfg.public_url, room: 'room', jwt: 'signed-token', moderator };
  const response = pending ? new Promise(resolve => { tokenResolve = resolve; }) : Promise.resolve({ ok: true, json: async () => token });
  const context = { document, URL, Date: Clock, SynctxJitsiCore: core, JitsiMeetExternalAPI: Jitsi,
    fetch: async () => response, location: {},
    CustomEvent: class { constructor(type, { detail }) { this.type = type; this.detail = detail; } },
    setTimeout: fn => { const id = ++timerId; timers.set(id, fn); return id; },
    clearTimeout: id => timers.delete(id), setInterval: () => {},
    addEventListener: (key, fn) => { windowEvents[key] = fn; },
  };
  context.window = context;
  vm.runInNewContext(source, context);
  const message = (sender, changes = {}) => instance.events.endpointTextMessageReceived({
    senderInfo: { id: sender }, eventData: { text: JSON.stringify({ schema: 'synctx-follow/1', doc: cfg.doc, ts: now, anchor: anchor.id, ...changes }) },
  });
  return { button, body, anchor, document, windowEvents, documentEvents, message, timers,
    tick() { const [id, fn] = timers.entries().next().value; timers.delete(id); fn(); },
    get instance() { return instance; }, get now() { return now; }, advance: ms => { now += ms; },
    resolveToken: () => tokenResolve({ ok: true, json: async () => token }) };
}

test('joining uses configured script, JWT and accessible controls while preserving iframe permissions', async () => {
  const env = setup();
  await env.button.click();
  assert.equal(env.document.head.children[0].src, 'https://meet.synctx.local:8443/external_api.js');
  assert.equal(env.instance.domain, 'meet.synctx.local:8443');
  assert.equal(env.instance.options.jwt, 'signed-token');
  assert.equal(env.instance.iframe.title, 'Jitsi 영상 회의');
  assert.equal(env.instance.iframe.attrs.allow, 'camera; microphone; display-capture; fullscreen; autoplay; clipboard-write');
  const panel = env.body.children[0];
  assert.equal(panel.attrs.role, 'region');
  assert.equal(panel.attrs['aria-label'], '영상 회의');
  assert.equal(panel.children[0].focused, true);
  assert.equal(panel.children[2].attrs['aria-pressed'], 'true');
  panel.children[1].click();
  assert.equal(env.instance.disposed, true);
  assert.equal(panel.hidden, true);
  assert.equal(env.button.focused, true);
});

test('Escape hides only the focused video panel, keeps the call, and leaves other panels alone', async () => {
  const env = setup();
  await env.button.click();
  const panel = env.body.children[0];
  assert.equal(env.windowEvents.keydown, undefined);
  assert.ok(panel.events.keydown);
  const escape = { key: 'Escape', preventDefault() {}, stopPropagation() { assert.fail('must not stop propagation'); } };
  new Element('textarea').focus();
  panel.events.keydown(escape);
  assert.equal(panel.hidden, false);
  panel.children[2].focus();
  panel.events.keydown(escape);
  assert.equal(panel.hidden, true);
  assert.equal(env.instance.disposed, undefined);
  assert.equal(env.button.focused, true);
  const original = env.instance;
  await env.button.click();
  assert.equal(env.instance, original);
  assert.equal(panel.hidden, false);
  panel.children[1].click();
  assert.equal(env.instance.disposed, true);
});

test('attendee verifies remote roles through cached rooms, ignores local role events, and stops on scroll', async () => {
  const env = setup();
  await env.button.click();
  assert.equal(env.instance.getParticipantsInfo()[0].role, undefined);
  env.instance.localRoleChanged('moderator');
  await env.message('a');
  await env.message('missing');
  assert.equal(env.anchor.scrolled, undefined);
  await env.message('p');
  assert.equal(env.anchor.scrolled, 1);
  assert.equal(env.instance.roomCalls, 1);
  await env.message('p');
  assert.equal(env.anchor.scrolled, 1);
  env.advance(1000);
  env.windowEvents.scroll();
  await env.message('p');
  assert.equal(env.anchor.scrolled, 1);
  const follow = env.body.children[0].children[2];
  assert.equal(follow.attrs['aria-pressed'], 'false');
  follow.click();
  await env.message('p');
  assert.equal(env.anchor.scrolled, 2);
  env.instance.rooms.rooms[0].participants[0].role = 'participant';
  env.advance(1000);
  await env.message('p');
  assert.equal(env.anchor.scrolled, 2);
});

test('room lookup failure, malformed response, and unknown roles fail closed and recover after cache expiry', async () => {
  const env = setup();
  await env.button.click();
  env.instance.roomError = true;
  await env.message('p');
  assert.equal(env.anchor.scrolled, undefined);
  env.advance(1000);
  env.instance.roomError = false;
  env.instance.rooms = {};
  await env.message('p');
  assert.equal(env.anchor.scrolled, undefined);
  env.advance(1000);
  env.instance.rooms = { rooms: [{ participants: [{ id: 'p' }] }] };
  await env.message('p');
  assert.equal(env.anchor.scrolled, undefined);
  env.advance(1000);
  env.instance.rooms.rooms[0].participants[0].role = 'moderator';
  await env.message('p');
  assert.equal(env.anchor.scrolled, 1);
});

test('room role snapshots expire after one second and local role events cannot prolong trust', async () => {
  const env = setup();
  await env.button.click();
  await env.message('p');
  env.instance.rooms = { rooms: [{ participants: [{ id: 'p', role: 'participant' }] }] };
  env.advance(999);
  await env.message('p');
  assert.equal(env.anchor.scrolled, 2);
  assert.equal(env.instance.roomCalls, 1);
  env.instance.localRoleChanged('moderator');
  env.advance(1);
  await env.message('p');
  assert.equal(env.anchor.scrolled, 2);
  assert.equal(env.instance.roomCalls, 2);
});

test('concurrent messages share room lookup and reject out of order timestamps per sender', async () => {
  const env = setup();
  await env.button.click();
  await Promise.all([env.message('p'), env.message('p', { ts: env.now - 1 }), env.message('p', { ts: env.now + 1 })]);
  assert.equal(env.instance.roomCalls, 1);
  assert.equal(env.anchor.scrolled, 2);
  env.instance.rooms.rooms[0].participants.push({ id: 'other', role: 'moderator' });
  env.instance.events.participantJoined({ id: 'other' });
  await env.message('other', { ts: env.now - 300000 });
  assert.equal(env.anchor.scrolled, 3);
});

for (const skew of [-300000, 300000]) {
  test(`following tolerates a sender clock skew of ${skew}ms and ignores replay`, async () => {
    const env = setup();
    await env.button.click();
    await env.message('p', { ts: env.now + skew });
    assert.equal(env.anchor.scrolled, 1);
    env.advance(1000);
    await env.message('p', { ts: env.now + skew - 1000 });
    assert.equal(env.anchor.scrolled, 1);
    await env.message('p', { ts: env.now + skew });
    assert.equal(env.anchor.scrolled, 2);
  });
}

test('late room results cannot move the document after closing or disabling follow', async () => {
  for (const action of ['close', 'unfollow', 'delay']) {
    const env = setup();
    await env.button.click();
    let resolve;
    env.instance.rooms = new Promise(done => { resolve = done; });
    const receiving = env.message('p');
    const panel = env.body.children[0];
    if (action === 'close') panel.children[1].click();
    if (action === 'unfollow') panel.children[2].click();
    if (action === 'delay') env.advance(15001);
    resolve({ rooms: [{ participants: [{ id: 'p', role: 'moderator' }] }] });
    await receiving;
    assert.equal(env.anchor.scrolled, undefined);
  }
});

test('participant leaving invalidates cached roles and clears sender timestamps', async () => {
  const env = setup();
  await env.button.click();
  await env.message('p');
  assert.equal(env.anchor.scrolled, 1);
  env.instance.events.participantLeft({ id: 'p' });
  env.instance.rooms = { rooms: [] };
  await env.message('p', { ts: env.now + 1 });
  assert.equal(env.anchor.scrolled, 1);
  env.instance.rooms = { rooms: [{ participants: [{ id: 'p', role: 'moderator' }] }] };
  env.instance.events.participantJoined({ id: 'p' });
  await env.message('p');
  assert.equal(env.anchor.scrolled, 2);
});

test('moderator sends position to new participants with the existing trailing throttle', async () => {
  const env = setup({ moderator: true, presenting: true });
  await env.button.click();
  assert.equal(env.body.children[0].children[2].hidden, true);
  env.instance.events.dataChannelOpened();
  env.tick();
  assert.equal(env.instance.sent.length, 1);
  assert.equal(env.instance.sent[0][0], 'sendEndpointTextMessage');
  assert.equal(JSON.parse(env.instance.sent[0][2]).slide, 2);
  env.instance.events.participantJoined({ id: 'new' });
  env.instance.events.participantJoined({ id: 'newer' });
  assert.equal(env.timers.size, 1);
  env.tick();
  assert.equal(env.instance.sent.length, 1);
  env.advance(250); env.tick();
  assert.equal(env.instance.sent.length, 2);
  assert.equal(JSON.parse(env.instance.sent[1][2]).slide, 2);
  env.documentEvents['synctx-slide-changed']({ detail: { slide: 3 } });
  env.tick();
  assert.equal(env.instance.sent.length, 2);
  env.advance(250); env.tick();
  assert.equal(env.instance.sent.length, 3);
  assert.equal(JSON.parse(env.instance.sent[2][2]).slide, 3);
});

test('following slides dispatches only validated moderator positions', async () => {
  const env = setup({ presenting: true });
  await env.button.click();
  await env.message('a', { anchor: undefined, slide: 3 });
  assert.equal(env.document.lastEvent, undefined);
  await env.message('p', { anchor: undefined, slide: 3 });
  assert.equal(env.document.lastEvent.type, 'synctx-follow-slide');
  assert.equal(env.document.lastEvent.detail.slide, 3);
});

test('closing while the token request is pending prevents late iframe creation', async () => {
  const env = setup({ pending: true });
  const joining = env.button.click();
  env.body.children[0].children[1].click();
  env.resolveToken();
  await joining;
  assert.equal(env.instance, undefined);
  assert.equal(env.body.children[0].hidden, true);
});

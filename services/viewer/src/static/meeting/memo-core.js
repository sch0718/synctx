/* synctx 회의 모드: DOM이 필요 없는 메모 로직. 브라우저와 node --test가 같은 파일을 쓴다. */
(function (root) {
  'use strict';

  var BACKSLASH = String.fromCharCode(92);
  var SCHEMA = 1;
  var LIMITS = { text: 10000, owner: 200, quote: 1000 };
  var KINDS = ['note', 'decision', 'action'];
  var SCRIPT_UNSAFE_RE = new RegExp('[<>&' + String.fromCharCode(0x2028, 0x2029) + ']', 'g');
  var ID_CHARS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';

  function nowIso() {
    return new Date().toISOString();
  }

  function newId() {
    var bytes = new Uint8Array(10);
    var cryptoApi = root.crypto;
    if (cryptoApi && cryptoApi.getRandomValues) {
      cryptoApi.getRandomValues(bytes);
    } else {
      for (var i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256);
    }
    var out = 'm_';
    for (var j = 0; j < bytes.length; j++) out += ID_CHARS.charAt(bytes[j] % ID_CHARS.length);
    return out;
  }

  function timeOf(iso) {
    var t = Date.parse(iso);
    return isNaN(t) ? 0 : t;
  }

  function compareTime(a, b) {
    return timeOf(a) - timeOf(b);
  }

  function stripRuntime(memo) {
    var out = {};
    Object.keys(memo).forEach(function (key) {
      if (key.charAt(0) !== '_') out[key] = memo[key];
    });
    return out;
  }

  function encodeForScript(value) {
    return JSON.stringify(value).replace(SCRIPT_UNSAFE_RE, function (ch) {
      return BACKSLASH + 'u' + ('0000' + ch.charCodeAt(0).toString(16)).slice(-4);
    });
  }

  function makeAnchor(text, start, end) {
    return { start: start, end: end, quote: text.slice(start, Math.min(end, start + LIMITS.quote)) };
  }

  function locate(text, anchor) {
    if (!anchor) return null;
    var length = Math.max(0, anchor.end - anchor.start);
    var quote = anchor.quote || '';
    var lost = { start: anchor.start, end: anchor.start, lost: true };
    if (!quote) return lost;
    function at(start) {
      return { start: start, end: Math.min(start + length, text.length), lost: false };
    }
    if (text.slice(anchor.start, anchor.start + quote.length) === quote) return at(anchor.start);
    var best = -1;
    var bestDistance = Infinity;
    var idx = text.indexOf(quote);
    while (idx !== -1) {
      var distance = Math.abs(idx - anchor.start);
      if (distance < bestDistance) {
        best = idx;
        bestDistance = distance;
      }
      idx = text.indexOf(quote, idx + 1);
    }
    return best === -1 ? lost : at(best);
  }

  function liveMemos(memos) {
    return memos.filter(function (m) { return !m.deleted_at; });
  }

  function summarize(memos, positions) {
    positions = positions || {};
    function pos(m) {
      return m.anchor && positions[m.id] != null ? positions[m.id] : Infinity;
    }
    var sorted = liveMemos(memos).sort(function (a, b) {
      var pa = pos(a);
      var pb = pos(b);
      if (pa !== pb) return pa < pb ? -1 : 1;
      return compareTime(a.created_at, b.created_at);
    });
    return {
      decisions: sorted.filter(function (m) { return m.kind === 'decision'; }),
      actions: sorted.filter(function (m) { return m.kind === 'action'; }),
      notes: sorted.filter(function (m) { return m.kind === 'note' && m.anchor; }),
      general: sorted.filter(function (m) { return m.kind === 'note' && !m.anchor; })
    };
  }

  root.SynctxMemoCore = {
    SCHEMA: SCHEMA,
    LIMITS: LIMITS,
    KINDS: KINDS,
    nowIso: nowIso,
    newId: newId,
    compareTime: compareTime,
    stripRuntime: stripRuntime,
    encodeForScript: encodeForScript,
    makeAnchor: makeAnchor,
    locate: locate,
    liveMemos: liveMemos,
    summarize: summarize
  };
})(typeof globalThis !== 'undefined' ? globalThis : window);

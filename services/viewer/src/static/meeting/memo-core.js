/* synctx 회의 모드: DOM이 필요 없는 메모 로직. 브라우저와 node --test가 같은 파일을 쓴다. */
(function (root) {
  'use strict';

  var BACKSLASH = String.fromCharCode(92);
  var SCHEMA = 1;
  var LIMITS = { text: 10000, owner: 200, quote: 1000 };
  var KINDS = ['note', 'decision', 'action'];
  var SCRIPT_UNSAFE_RE = new RegExp('[<>&' + String.fromCharCode(0x2028, 0x2029) + ']', 'g');
  var ID_CHARS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';

  var EMBED_SCRIPT_ID = 'meeting-' + 'embed';
  var ID_RE = /^m_[A-Za-z0-9]{6,32}$/;
  var DUE_RE = /^\d{4}-\d{2}-\d{2}$/;
  var ISO_DATETIME_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})$/;
  var MD_DATA_RE = new RegExp('<!--' + '\\s*' + 'synctx-' + 'memo-data' + '\\s+([\\s\\S]*?)\\s*-->', 'g');
  var EMBED_RE = new RegExp('<script id="' + EMBED_SCRIPT_ID + '">\\s*window\\.MEETING_EMBED\\s*=\\s*([\\s\\S]*?);?\\s*</' + 'script>', 'g');

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

  function sortByCreated(memos) {
    return memos.slice().sort(function (a, b) { return compareTime(a.created_at, b.created_at); });
  }

  function exportPayload(store, exportedAt) {
    return {
      schema: SCHEMA,
      doc: store.doc,
      exported_at: exportedAt,
      memos: sortByCreated(liveMemos(store.memos)).map(stripRuntime)
    };
  }

  function serialize(store) {
    return JSON.stringify({ schema: SCHEMA, doc: store.doc, memos: sortByCreated(store.memos).map(stripRuntime) });
  }

  // 사람이 읽는 부분에 HTML 주석 구분자가 들어가면 데이터 주석과 섞이고 렌더링에서 글이 숨는다
  function readable(value) {
    return String(value == null ? '' : value).replace(/<!--/g, '&lt;!--').replace(/-->/g, '--&gt;');
  }

  function oneLine(value) {
    return readable(value).replace(/\r?\n/g, ' ');
  }

  function tableCell(value) {
    return readable(value).replace(/\|/g, BACKSLASH + '|').replace(/\r?\n/g, '<br>');
  }

  function quoteSuffix(m) {
    return m.anchor ? ' — 인용: "' + oneLine(m.anchor.quote) + '"' : '';
  }

  function toMarkdown(store, meta) {
    meta = meta || {};
    var exportedAt = meta.exported_at || nowIso();
    var payload = exportPayload(store, exportedAt);
    var groups = summarize(payload.memos, meta.positions);
    var title = (store.doc && store.doc.title) || '회의';
    var out = ['# ' + oneLine(title) + ' — 회의 메모', ''];
    if (meta.starts_at) out.push('- 회의 일시: ' + meta.starts_at);
    out.push('- 내보낸 시각: ' + exportedAt, '', '## 결정사항', '');
    if (groups.decisions.length) {
      groups.decisions.forEach(function (m, i) { out.push((i + 1) + '. ' + oneLine(m.text) + quoteSuffix(m)); });
    } else {
      out.push('없음');
    }
    out.push('', '## 액션 아이템', '');
    if (groups.actions.length) {
      out.push('| 할 일 | 담당자 | 기한 | 인용 |', '|---|---|---|---|');
      groups.actions.forEach(function (m) {
        out.push('| ' + tableCell(m.text) + ' | ' + tableCell(m.owner) + ' | ' + tableCell(m.due) + ' | '
          + tableCell(m.anchor ? m.anchor.quote : '') + ' |');
      });
    } else {
      out.push('없음');
    }
    out.push('', '## 메모', '');
    if (groups.notes.length) {
      groups.notes.forEach(function (m) { out.push('- ' + oneLine(m.text) + quoteSuffix(m)); });
    } else {
      out.push('없음');
    }
    out.push('', '## 전체 메모', '');
    if (groups.general.length) {
      groups.general.forEach(function (m) { out.push('- ' + oneLine(m.text)); });
    } else {
      out.push('없음');
    }
    out.push('', '<!-- ' + 'synctx-' + 'memo-data ' + encodeForScript(payload) + ' -->', '');
    return out.join('\n');
  }

  function isIsoDateTime(value) {
    return typeof value === 'string' && ISO_DATETIME_RE.test(value) && !isNaN(Date.parse(value));
  }

  function validateMemo(m) {
    if (!m || typeof m !== 'object') return 'object';
    if (typeof m.id !== 'string' || !ID_RE.test(m.id)) return 'id';
    if (KINDS.indexOf(m.kind) === -1) return 'kind';
    if (typeof m.text !== 'string' || m.text.length > LIMITS.text) return 'text';
    if (m.owner != null && (typeof m.owner !== 'string' || m.owner.length > LIMITS.owner)) return 'owner';
    if (m.due != null && m.due !== '' && (typeof m.due !== 'string' || !DUE_RE.test(m.due))) return 'due';
    if (!isIsoDateTime(m.created_at)) return 'created_at';
    if (!isIsoDateTime(m.updated_at)) return 'updated_at';
    if (m.deleted_at != null && !isIsoDateTime(m.deleted_at)) return 'deleted_at';
    if (m.anchor != null) {
      var a = m.anchor;
      if (typeof a !== 'object' || !Number.isInteger(a.start) || !Number.isInteger(a.end) || a.start < 0 || a.end < a.start) return 'anchor';
      if (typeof a.quote !== 'string' || a.quote.length > LIMITS.quote) return 'anchor.quote';
    }
    return null;
  }

  function lastMatch(re, text) {
    re.lastIndex = 0;
    var found = null;
    var m;
    while ((m = re.exec(text))) found = m;
    return found;
  }

  function parseImport(fileText) {
    // 데이터 블록은 내보낸 파일의 맨 끝에 있으므로 마지막 것을 쓴다
    var match = lastMatch(MD_DATA_RE, fileText) || lastMatch(EMBED_RE, fileText);
    if (!match) return { error: '메모 데이터를 찾지 못했습니다' };
    var data;
    try {
      data = JSON.parse(match[1]);
    } catch (e) {
      return { error: '메모 데이터 형식이 올바르지 않습니다' };
    }
    if (!data || data.schema !== SCHEMA) return { error: '지원하지 않는 메모 형식입니다(schema)' };
    if (!Array.isArray(data.memos)) return { error: '메모 목록(memos)이 올바르지 않습니다' };
    if (!data.doc || !data.doc.repo_id || !data.doc.meeting_id) return { error: '회의 정보(doc)가 없습니다' };
    var memos = [];
    var invalid = [];
    data.memos.forEach(function (m) {
      var reason = validateMemo(m);
      if (reason) invalid.push({ memo: m, reason: reason });
      else memos.push(stripRuntime(m));
    });
    return { doc: data.doc, memos: memos, invalid: invalid };
  }

  function merge(current, incoming, opts) {
    opts = opts || {};
    var now = opts.now || nowIso();
    var result = { memos: current.slice(), added: [], updated: [], skipped: [], restored: [], deletedConflicts: [] };
    var index = {};
    result.memos.forEach(function (m, i) { index[m.id] = i; });
    incoming.forEach(function (inc) {
      if (!(inc.id in index)) {
        index[inc.id] = result.memos.length;
        result.memos.push(inc);
        result.added.push(inc);
        return;
      }
      var cur = result.memos[index[inc.id]];
      var newer = compareTime(inc.updated_at, cur.updated_at) > 0;
      if (cur.deleted_at && !inc.deleted_at && !newer) {
        result.deletedConflicts.push(inc);
        if (opts.restoreDeleted) {
          var revived = stripRuntime(inc);
          delete revived.deleted_at;
          revived.updated_at = now;
          result.memos[index[inc.id]] = revived;
          result.restored.push(revived);
        } else {
          result.skipped.push(inc);
        }
        return;
      }
      if (newer) {
        result.memos[index[inc.id]] = inc;
        result.updated.push(inc);
      } else {
        result.skipped.push(inc);
      }
    });
    return result;
  }

  root.SynctxMemoCore = {
    SCHEMA: SCHEMA,
    LIMITS: LIMITS,
    KINDS: KINDS,
    EMBED_SCRIPT_ID: EMBED_SCRIPT_ID,
    nowIso: nowIso,
    newId: newId,
    compareTime: compareTime,
    stripRuntime: stripRuntime,
    encodeForScript: encodeForScript,
    makeAnchor: makeAnchor,
    locate: locate,
    liveMemos: liveMemos,
    summarize: summarize,
    exportPayload: exportPayload,
    serialize: serialize,
    toMarkdown: toMarkdown,
    validateMemo: validateMemo,
    parseImport: parseImport,
    merge: merge
  };
})(typeof globalThis !== 'undefined' ? globalThis : window);

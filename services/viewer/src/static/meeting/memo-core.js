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
  var WS_RE = /\s/;
  var MD_MARKER = 'synctx-' + 'memo-data';
  var MD_OPEN = '<!--';
  var MD_CLOSE = '-->';
  var EMBED_OPEN = '<script id="' + EMBED_SCRIPT_ID + '">';
  var SCRIPT_CLOSE = '</' + 'script>';
  var EMBED_VAR = 'window.MEETING_EMBED';

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
    if (meta.starts_at) out.push('- 회의 일시: ' + oneLine(meta.starts_at));
    out.push('- 내보낸 시각: ' + oneLine(exportedAt), '', '## 결정사항', '');
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

  // 정규식 백트래킹을 피하려고 indexOf/lastIndexOf로 선형 탐색한다
  function findMarkdownData(text) {
    var lastClose = text.lastIndexOf(MD_CLOSE);
    var idx = text.lastIndexOf(MD_MARKER);
    while (idx !== -1) {
      var from = idx + MD_MARKER.length;
      var before = idx;
      while (before > 0 && WS_RE.test(text.charAt(before - 1))) before--;
      var opened = before >= MD_OPEN.length && text.slice(before - MD_OPEN.length, before) === MD_OPEN;
      if (opened && from < text.length && WS_RE.test(text.charAt(from)) && lastClose >= from) {
        return text.slice(from, text.indexOf(MD_CLOSE, from)).trim();
      }
      idx = idx === 0 ? -1 : text.lastIndexOf(MD_MARKER, idx - 1);
    }
    return null;
  }

  function allIndexes(text, needle) {
    var found = [];
    var idx = text.indexOf(needle);
    while (idx !== -1) {
      found.push(idx);
      idx = text.indexOf(needle, idx + needle.length);
    }
    return found;
  }

  function skipSpaces(text, pos) {
    while (pos < text.length && WS_RE.test(text.charAt(pos))) pos++;
    return pos;
  }

  // 여는 태그를 뒤에서부터 보며 투 포인터로 각 태그 뒤의 첫 닫는 태그를 찾는다
  function findEmbedData(text) {
    var opens = allIndexes(text, EMBED_OPEN);
    var closes = allIndexes(text, SCRIPT_CLOSE);
    var j = closes.length;
    for (var i = opens.length - 1; i >= 0; i--) {
      var from = opens[i] + EMBED_OPEN.length;
      while (j > 0 && closes[j - 1] >= from) j--;
      if (j === closes.length) continue;
      var close = closes[j];
      var pos = skipSpaces(text, from);
      if (text.substr(pos, EMBED_VAR.length) !== EMBED_VAR) continue;
      pos = skipSpaces(text, pos + EMBED_VAR.length);
      if (text.charAt(pos) !== '=') continue;
      pos = skipSpaces(text, pos + 1);
      var data = text.slice(pos, close).trim();
      if (data.charAt(data.length - 1) === ';') data = data.slice(0, -1);
      return data.trim();
    }
    return null;
  }

  function parseImport(fileText) {
    // 데이터 블록은 내보낸 파일의 맨 끝에 있으므로 마지막 것을 쓴다
    var raw = findMarkdownData(fileText);
    if (raw === null) raw = findEmbedData(fileText);
    if (raw === null) return { error: '메모 데이터를 찾지 못했습니다' };
    var data;
    try {
      data = JSON.parse(raw);
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

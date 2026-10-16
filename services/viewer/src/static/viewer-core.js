/* Viewer 화면 동작 중 DOM 없이 시험할 수 있는 순수 함수. viewer.js가 쓴다. */
(function (root) {
  'use strict';

  var RECENT_LIMIT = 8;

  function matchFiles(paths, query, limit) {
    var terms = String(query || '').trim().toLowerCase().split(/\s+/).filter(Boolean);
    if (!terms.length) return [];
    var scored = [];
    for (var i = 0; i < paths.length; i++) {
      var path = String(paths[i]);
      var lower = path.toLowerCase();
      var ok = true;
      for (var t = 0; t < terms.length; t++) {
        if (lower.indexOf(terms[t]) === -1) { ok = false; break; }
      }
      if (!ok) continue;
      var name = lower.slice(lower.lastIndexOf('/') + 1);
      var pos = name.indexOf(terms[0]);
      scored.push({ path: path, score: pos === 0 ? 0 : pos > 0 ? 1 : 2 });
    }
    scored.sort(function (a, b) {
      if (a.score !== b.score) return a.score - b.score;
      if (a.path.length !== b.path.length) return a.path.length - b.path.length;
      return a.path < b.path ? -1 : a.path > b.path ? 1 : 0;
    });
    return scored.slice(0, limit || 20).map(function (s) { return s.path; });
  }

  function addRecent(list, entry, now) {
    var ref = entry.ref || '';
    var items = (Array.isArray(list) ? list : []).filter(function (x) {
      return x && !(x.repo === entry.repo && x.path === entry.path && (x.ref || '') === ref);
    });
    items.unshift({
      repo: entry.repo,
      path: entry.path,
      ref: ref,
      title: String(entry.title || entry.path).slice(0, 200),
      at: now,
    });
    return items.slice(0, RECENT_LIMIT);
  }

  function parseRecent(raw) {
    try {
      var value = JSON.parse(raw);
      return Array.isArray(value) ? value : [];
    } catch (e) {
      return [];
    }
  }

  function createFileLoader(fetchFiles) {
    var files = null;
    var pending = null;
    return {
      files: function () { return files; },
      load: function () {
        if (files) return Promise.resolve({ ok: true, files: files });
        if (!pending) {
          pending = Promise.resolve()
            .then(fetchFiles)
            .then(function (list) {
              files = Array.isArray(list) ? list : [];
              return { ok: true, files: files };
            }, function () {
              return { ok: false, files: null };
            })
            .then(function (result) { pending = null; return result; });
        }
        return pending;
      },
    };
  }

  function branchChoices(data, active) {
    var list = data && Array.isArray(data.branches) ? data.branches.filter(function (b) { return typeof b === 'string' && b; }) : [];
    if (!list.length) return null;
    if (active && list.indexOf(active) === -1) list.unshift(active);
    return list.map(function (b) {
      return { value: b, label: b === data.default ? b + ' (기본)' : b, selected: b === active };
    });
  }

  root.SynctxViewerCore = {
    RECENT_LIMIT: RECENT_LIMIT,
    matchFiles: matchFiles,
    addRecent: addRecent,
    parseRecent: parseRecent,
    createFileLoader: createFileLoader,
    branchChoices: branchChoices,
  };
})(typeof globalThis !== 'undefined' ? globalThis : this);

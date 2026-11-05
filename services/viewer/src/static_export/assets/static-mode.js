/* 정적 내보내기 사이트에서만 쓰는 shim: API 주소를 정적 JSON으로 바꾸고 테마 선택을 반영한다. viewer.js 뒤, </body> 앞에 실린다. */
(function (root) {
  'use strict';

  var THEME_KEY = 'synctx-theme';
  var API_PATH = /^\/view\/api\/repos\/([^/]+)\/(tree|files)$/;

  function rewriteApiUrl(raw, base) {
    var url;
    var origin;
    try {
      url = new URL(String(raw), base);
      origin = new URL(base).origin;
    } catch (e) { return null; }
    if (url.origin !== origin) return null;
    var match = API_PATH.exec(url.pathname);
    if (!match) return null;
    var prefix = '/view/api/repos/' + match[1];
    if (match[2] === 'files') return prefix + '/files.json';
    var path = url.searchParams.get('path') || '';
    if (!path) return prefix + '/tree.json';
    var segments = path.split('/');
    if (segments.some(function (s) { return s === '..' || s === '.'; })) return null;
    return prefix + '/tree/' + segments.map(encodeURIComponent).join('/') + '.json';
  }

  function resolveTheme(stored, table, fallback) {
    return typeof stored === 'string' && Object.prototype.hasOwnProperty.call(table, stored) ? stored : fallback;
  }

  function themeEntry(stored, table, fallback) {
    var name = resolveTheme(stored, table, fallback);
    var entry = table[name];
    return { name: name, css: entry.css, hljs: entry.hljs, mermaid: entry.mermaid };
  }

  function storageGet(win, key) { try { return win.localStorage.getItem(key); } catch (e) { return null; } }
  function storageSet(win, key, value) { try { win.localStorage.setItem(key, value); } catch (e) { return; } }

  function installFetch(win) {
    var original = win.fetch;
    if (typeof original !== 'function') return;
    win.fetch = function (input, init) {
      var raw = typeof input === 'string' || input instanceof URL ? String(input) : (input && input.url);
      var target = raw ? rewriteApiUrl(raw, win.location.href) : null;
      return original.call(win, target || input, init);
    };
  }

  function requestedPath(href) {
    try { return new URL(href).searchParams.get('path') || ''; } catch (e) { return ''; }
  }

  function expandRequestedPath(win) {
    var path = requestedPath(win.location.href);
    if (!path || typeof win.expandToPath !== 'function') return;
    var tree = win.document.getElementById('file-tree');
    if (!tree || (tree.dataset && tree.dataset.expandPath)) return;
    var run = function () { win.expandToPath(path); };
    if (win.document.readyState === 'loading') win.document.addEventListener('DOMContentLoaded', run);
    else run();
  }

  function applyTheme(win, entry) {
    var doc = win.document;
    doc.documentElement.setAttribute('data-theme', entry.name);
    var css = doc.getElementById('theme-css');
    if (css) css.setAttribute('href', entry.css);
    var hljs = doc.getElementById('hljs-css');
    if (hljs) hljs.setAttribute('href', entry.hljs);
    Array.prototype.forEach.call(doc.querySelectorAll('select[data-action="theme"]'), function (select) {
      select.value = entry.name;
    });
  }

  function installThemeSwitch(win, config) {
    win.document.addEventListener('change', function (event) {
      var el = event.target;
      if (!el || !el.getAttribute || el.getAttribute('data-action') !== 'theme') return;
      event.stopImmediatePropagation();
      if (!Object.prototype.hasOwnProperty.call(config.table, el.value)) return;
      storageSet(win, THEME_KEY, el.value);
      win.location.reload();
    }, true);
  }

  function init(win) {
    var config = win.SYNCTX_STATIC;
    if (!config || !config.table) return false;
    installFetch(win);
    expandRequestedPath(win);
    applyTheme(win, themeEntry(storageGet(win, THEME_KEY), config.table, config.defaultTheme));
    installThemeSwitch(win, config);
    return true;
  }

  root.SynctxStaticMode = {
    rewriteApiUrl: rewriteApiUrl,
    resolveTheme: resolveTheme,
    themeEntry: themeEntry,
    requestedPath: requestedPath,
    init: init,
  };

  if (root.SYNCTX_STATIC) init(root);
})(typeof globalThis !== 'undefined' ? globalThis : this);

/* Viewer 화면 동작: 테마·저장소 이동, 서랍, 트리, 파일 찾기, 목차 추적, 알림, Marp, 최근 문서, 단축키. */
(function () {
  'use strict';

  var core = window.SynctxViewerCore;
  var RECENT_KEY = 'synctx-recent';
  var THEME_KEY = 'synctx-theme';

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $all(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  // 사생활 보호 모드 등에서 저장소 접근이 예외를 던져도 화면은 동작해야 한다
  function storageGet(key) { try { return window.localStorage.getItem(key); } catch (e) { return null; } }
  function storageSet(key, value) { try { window.localStorage.setItem(key, value); } catch (e) { return; } }

  function escapeHtml(text) {
    return String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function encodePath(path) { return String(path).split('/').map(encodeURIComponent).join('/'); }
  function _refParam(sep) { return ACTIVE_REF ? sep + 'ref=' + encodeURIComponent(ACTIVE_REF) : ''; }
  window.escapeHtml = escapeHtml;
  window.encodePath = encodePath;

  /* ── 알림 ── */
  function synctxToast(opts) {
    var region = $('#toast-region');
    var noop = { close: function () {} };
    if (!region) return noop;
    var el = document.createElement('div');
    el.className = 'toast toast-' + opts.kind;
    el.setAttribute('role', opts.kind === 'error' ? 'alert' : 'status');
    var mark = document.createElement('span');
    mark.className = 'toast-mark';
    mark.setAttribute('aria-hidden', 'true');
    var text = document.createElement('div');
    text.className = 'toast-text';
    var title = document.createElement('div');
    title.className = 'toast-title';
    title.textContent = opts.title;
    var body = document.createElement('div');
    body.className = 'toast-body';
    body.textContent = opts.body || '';
    text.appendChild(title);
    text.appendChild(body);
    el.appendChild(mark);
    el.appendChild(text);
    if (opts.actionLabel) {
      var action = document.createElement('button');
      action.type = 'button';
      action.className = 'btn';
      action.textContent = opts.actionLabel;
      action.addEventListener('click', function () { close(); opts.onAction(); });
      el.appendChild(action);
    }
    region.appendChild(el);
    var timer = null;
    if (opts.kind === 'progress') {
      var started = Date.now();
      body.textContent = '0초 지남';
      timer = window.setInterval(function () {
        body.textContent = Math.round((Date.now() - started) / 1000) + '초 지남';
      }, 1000);
    } else if (opts.timeout) {
      timer = window.setTimeout(close, opts.timeout);
    }
    function close() { window.clearInterval(timer); window.clearTimeout(timer); el.remove(); }
    return { close: close };
  }
  window.synctxToast = synctxToast;

  /* ── 테마·저장소·브랜치 ── */
  function applyTheme(theme) {
    storageSet(THEME_KEY, theme);
    if (document.body.dataset.themeMode === 'query') {
      var u = new URL(window.location);
      u.searchParams.set('theme', theme);
      window.location.href = u.toString();
    } else {
      document.documentElement.setAttribute('data-theme', theme);
      $all('select[data-action="theme"]').forEach(function (s) { s.value = theme; });
    }
  }
  function initSelects() {
    if (document.body.dataset.themeMode !== 'query') {
      var saved = storageGet(THEME_KEY);
      if (saved) $all('select[data-action="theme"]').forEach(function (s) { s.value = saved; });
    }
    document.addEventListener('change', function (e) {
      var el = e.target;
      var action = el.getAttribute && el.getAttribute('data-action');
      if (action === 'theme') applyTheme(el.value);
      else if (action === 'repo') window.location.href = VIEW_PREFIX + '/' + (el.value ? encodeURIComponent(el.value) : '');
      else if (action === 'branch' && ACTIVE_REPO) {
        window.location.href = VIEW_PREFIX + '/' + encodeURIComponent(ACTIVE_REPO) + (el.value ? '?ref=' + encodeURIComponent(el.value) : '');
      }
    });
  }

  /* ── 서랍(767px 이하)과 탭(1099px 이하) ── */
  var DRAWER = window.matchMedia('(max-width: 767px)');
  var WIDE = window.matchMedia('(min-width: 1100px)');
  var blocked = [];

  function showNavTab(name) {
    $all('[data-nav-tab]').forEach(function (t) { t.setAttribute('aria-selected', String(t.dataset.navTab === name)); });
    $all('[data-nav-panel]').forEach(function (p) { p.hidden = p.dataset.navPanel !== name; });
    var panel = $('[data-nav-panel="toc"]');
  // 서버는 현재 브랜치만 그려 두므로, 화면이 뜬 뒤 전체 목록으로 바꾼다. 실패하면 현재 브랜치만 남긴다
  async function loadBranches() {
    var selects = $all('select[data-action="branch"]');
    if (!selects.length || !ACTIVE_REPO) return;
    try {
      var res = await fetch(VIEW_PREFIX + '/api/repos/' + encodeURIComponent(ACTIVE_REPO) + '/branches');
      if (!res.ok) return;
      var data = await res.json();
      selects.forEach(function (select) {
        var choices = core.branchChoices(data, select.value);
        if (!choices) return;
        select.textContent = '';
        choices.forEach(function (c) {
          var option = document.createElement('option');
          option.value = c.value;
          option.textContent = c.label;
          option.selected = c.selected;
          select.appendChild(option);
        });
      });
    } catch (e) { return; }
  }

    var source = $('.shell-toc .toc');
    if (name === 'toc' && panel && source && !panel.firstElementChild) panel.appendChild(source.cloneNode(true));
  }
  function navOpen() { var nav = $('#shell-nav'); return !!nav && nav.classList.contains('is-open'); }
  // 서랍 밖의 모든 요소(서랍의 조상마다 그 형제). 회의 메모 버튼·패널, 알림처럼 body에 붙는 요소도 포함한다
  function outsideNav(nav) {
    var out = [];
    for (var el = nav; el.parentElement && el !== document.body; el = el.parentElement) {
      Array.prototype.forEach.call(el.parentElement.children, function (sib) {
        if (sib !== el && !sib.classList.contains('nav-backdrop') && sib.tagName !== 'SCRIPT' && !sib.inert) out.push(sib);
      });
    }
    return out;
  }
  function blockOutside(nav, on) {
    blocked.forEach(function (el) { el.inert = false; });
    blocked = on ? outsideNav(nav) : [];
    blocked.forEach(function (el) { el.inert = true; });
  }
  function setNav(open, tab) {
    var nav = $('#shell-nav');
    if (!nav) return;
    if (WIDE.matches && tab === 'toc') {
      // 1100px 이상에서는 오른쪽 목차가 보이므로 왼쪽 탭을 바꾸지 않는다
      var link = $('.shell-toc .toc a');
      if (link) link.focus();
      return;
    }
    if (tab) showNavTab(tab);
    if (!DRAWER.matches) {
      // 768px 이상에서는 왼쪽 열이 늘 보이므로 서랍·배경막 없이 탭만 바꾸고 포커스를 옮긴다
      if (open) { var t = $('[data-nav-tab][aria-selected="true"]', nav); if (t) t.focus(); }
      return;
    }
    var btn = $('[data-action="toggle-nav"]');
    nav.classList.toggle('is-open', open);
    nav.inert = !open;
    blockOutside(nav, open);
    var backdrop = $('.nav-backdrop');
    if (backdrop) backdrop.hidden = !open;
    if (btn) {
      btn.setAttribute('aria-expanded', String(open));
      btn.setAttribute('aria-label', open ? '메뉴 닫기' : '메뉴 열기');
    }
    if (open) {
      var first = $('[data-nav-tab][aria-selected="true"]', nav) || $('select, a, button, input', nav);
      if (first) first.focus();
    } else if (btn) {
      btn.focus();
    }
  }
  function syncNavMode() {
    var nav = $('#shell-nav');
    if (!nav) return;
    nav.classList.remove('is-open');
    blockOutside(nav, false);
    var backdrop = $('.nav-backdrop');
    if (backdrop) backdrop.hidden = true;
    nav.inert = DRAWER.matches;
    var btn = $('[data-action="toggle-nav"]');
    if (btn) { btn.setAttribute('aria-expanded', 'false'); btn.setAttribute('aria-label', '메뉴 열기'); }
    if (WIDE.matches) showNavTab('files');
  }
  function initNav() {
    syncNavMode();
    DRAWER.addEventListener('change', syncNavMode);
    WIDE.addEventListener('change', syncNavMode);
  }

  /* ── 파일 트리 ── */
  var ICON_DIR = '<svg class="icon-dir" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M10 4H4a2 2 0 00-2 2v12a2 2 0 002 2h16a2 2 0 002-2V8a2 2 0 00-2-2h-8l-2-2z"/></svg>';
  var ICON_MD = '<svg class="icon-md" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>';
  var ICON_CODE = '<svg class="icon-code" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/><path d="m10 13-2 2 2 2"/><path d="m14 13 2 2-2 2"/></svg>';
  var ICON_BINARY = '<svg class="icon-binary" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>';
  var TOGGLE = '<svg class="tree-toggle" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="m9 18 6-6-6-6"/></svg>';
  var SPACER = '<svg viewBox="0 0 24 24" aria-hidden="true"></svg>';
  var FILE_ICONS = { markdown: ICON_MD, code: ICON_CODE, binary: ICON_BINARY };

  function toggleDir(el) {
    var children = el.nextElementSibling;
    var icon = el.querySelector('.tree-toggle');
    if (!children) return;
    children.classList.toggle('collapsed');
    var open = !children.classList.contains('collapsed');
    if (icon) icon.classList.toggle('open', open);
    el.setAttribute('aria-expanded', String(open));
    if (open && children.dataset.path && !children.dataset.loaded) loadSubtree(children, children.dataset.path);
  }

  function addTitles(root) {
    $all('.tree-entry .name', root).forEach(function (n) { if (!n.title) n.title = n.textContent.trim(); });
  }

  async function loadSubtree(container, path) {
    container.dataset.loaded = '1';
    container.innerHTML = '<div class="tree-note">불러오는 중…</div>';
    try {
      var res = await fetch(VIEW_PREFIX + '/api/repos/' + encodeURIComponent(ACTIVE_REPO) + '/tree?path=' + encodeURIComponent(path) + _refParam('&'));
      if (!res.ok) throw new Error('tree');
      var data = await res.json();
      var byName = function (a, b) { return a.name.localeCompare(b.name); };
      var dirs = data.entries.filter(function (e) { return e.type === 'dir'; }).sort(byName);
      var files = data.entries.filter(function (e) { return e.type === 'file'; }).sort(byName);
      var html = '';
      dirs.forEach(function (d) {
        html += '<button type="button" class="tree-entry tree-dir" aria-expanded="false">' + TOGGLE + ICON_DIR +
          '<span class="name">' + escapeHtml(d.name) + '/</span></button>' +
          '<div class="tree-children collapsed" data-path="' + escapeHtml(d.path) + '"></div>';
      });
      files.forEach(function (f) {
        var icon = FILE_ICONS[f.icon_type] || ICON_BINARY;
        if (f.viewable) {
          var href = VIEW_PREFIX + '/' + encodeURIComponent(ACTIVE_REPO) + '/' + encodePath(f.path) + _refParam('?');
          var isCurrent = !!window.SYNCTX_DOC && window.SYNCTX_DOC.path === f.path;
          html += '<div class="tree-entry' + (isCurrent ? ' current' : '') + '">' + SPACER + icon + '<span class="name"><a href="' + escapeHtml(href) + '"' +
            (isCurrent ? ' aria-current="page"' : '') + '>' + escapeHtml(f.name) + '</a></span></div>';
        } else {
          html += '<div class="tree-entry disabled">' + SPACER + icon + '<span class="name">' + escapeHtml(f.name) + '</span></div>';
        }
      });
      container.innerHTML = html || '<div class="tree-note">비어 있음</div>';
      addTitles(container);
    } catch (err) {
      container.dataset.loaded = '';
      container.innerHTML = '<div class="tree-note">불러오지 못했습니다. 폴더를 다시 눌러 보세요.</div>';
    }
  }

  function filterTree(query) {
    var q = String(query || '').toLowerCase();
    $all('#file-tree .tree-entry').forEach(function (el) {
      var name = el.querySelector('.name').textContent.toLowerCase();
      el.style.display = !q || name.indexOf(q) !== -1 ? '' : 'none';
    });
  }

  async function expandToPath(path) {
    var parts = String(path).split('/').filter(Boolean);
    var current = '';
    var target = null;
    for (var i = 0; i < parts.length; i++) {
      current = current ? current + '/' + parts[i] : parts[i];
      var container = $all('#file-tree .tree-children').filter(function (c) { return c.dataset.path === current; })[0];
      if (!container) break;
      var entry = container.previousElementSibling;
      container.classList.remove('collapsed');
      var icon = entry.querySelector('.tree-toggle');
      if (icon) icon.classList.add('open');
      entry.setAttribute('aria-expanded', 'true');
      if (!container.dataset.loaded) await loadSubtree(container, current);
      target = entry;
    }
    if (target) {
      target.classList.add('current');
      target.scrollIntoView({ block: 'center' });
    }
  }

  window.toggleDir = toggleDir;
  window.loadSubtree = loadSubtree;
  window.filterTree = filterTree;
  window.expandToPath = expandToPath;

  function initTree() {
    var tree = $('#file-tree');
    if (!tree) return;
    addTitles(tree);
    if (tree.dataset.expandPath) expandToPath(tree.dataset.expandPath);
    var current = $('.tree-entry.current', tree);
    var nav = $('#shell-nav');
    if (current && nav) nav.scrollTop = current.offsetTop - nav.clientHeight / 2;
  }

  /* ── 파일 찾기 ── */
  function initFinder() {
    var input = $('#finder-input');
    var list = $('#finder-results');
    if (!input || !list) return;
    var loader = core.createFileLoader(function () {
      return fetch(VIEW_PREFIX + '/api/repos/' + encodeURIComponent(ACTIVE_REPO) + '/files' + _refParam('?'))
        .then(function (res) { if (!res.ok) throw new Error('files'); return res.json(); })
        .then(function (data) { return data.files || []; });
    });
    var items = [];
    var active = -1;

    function setOpen(open) { list.hidden = !open; input.setAttribute('aria-expanded', String(open)); }
    function message(text) { items = []; active = -1; list.innerHTML = '<li class="finder-empty">' + escapeHtml(text) + '</li>'; setOpen(true); }
    function paint() {
      list.innerHTML = items.map(function (p, i) {
        return '<li role="option" id="finder-opt-' + i + '" aria-selected="' + (i === active) + '" data-path="' + escapeHtml(p) + '">' +
          '<span class="finder-name">' + escapeHtml(p.split('/').pop()) + '</span>' +
          '<span class="finder-path">' + escapeHtml(p) + '</span></li>';
      }).join('');
      input.setAttribute('aria-activedescendant', active >= 0 ? 'finder-opt-' + active : '');
      setOpen(true);
    }
    function render() {
      if (!ACTIVE_REPO) { message('저장소를 먼저 고르세요.'); return; }
      if (!input.value.trim()) { setOpen(false); return; }
      var files = loader.files();
      if (files === null) {
        message('파일 목록을 불러오는 중…');
        loader.load().then(function (result) {
          if (!input.value.trim()) return;
          if (result.ok) render();
          else message('파일 목록을 불러오지 못했습니다. 검색어를 고치면 다시 시도합니다.');
        });
        return;
      }
      items = core.matchFiles(files, input.value, 20);
      active = items.length ? 0 : -1;
      if (!items.length) { message('맞는 파일이 없습니다.'); return; }
      paint();
    }
    function go(path) {
      window.location.href = VIEW_PREFIX + '/' + encodeURIComponent(ACTIVE_REPO) + '/' + encodePath(path) + _refParam('?');
    }
    input.addEventListener('input', render);
    input.addEventListener('focus', function () { if (ACTIVE_REPO) loader.load(); });
    input.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        if (!items.length) return;
        e.preventDefault();
        active = (active + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
        paint();
      } else if (e.key === 'Enter') {
        if (active >= 0) { e.preventDefault(); go(items[active]); }
      } else if (e.key === 'Escape') {
        setOpen(false);
      }
    });
    list.addEventListener('mousedown', function (e) {
      var li = e.target.closest('li[data-path]');
      if (li) { e.preventDefault(); go(li.dataset.path); }
    });
    input.addEventListener('blur', function () { window.setTimeout(function () { setOpen(false); }, 150); });
  }
  function focusFinder() { var input = $('#finder-input'); if (input) { input.focus(); input.select(); } }

  /* ── 이 문서 목차: 보이는 절 표시 ── */
  function initTocSpy() {
    var source = $all('.shell-toc .toc a[href^="#"]');
    if (!source.length || !('IntersectionObserver' in window)) return;
    var ids = source.map(function (a) { return decodeURIComponent(a.getAttribute('href').slice(1)); });
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        // 원본 목차와 서랍에 복제한 목차를 함께 갱신한다
        $all('.toc a[href^="#"]').forEach(function (a) {
          if (decodeURIComponent(a.getAttribute('href').slice(1)) === entry.target.id) a.setAttribute('aria-current', 'location');
          else a.removeAttribute('aria-current');
        });
      });
    }, { rootMargin: '-64px 0px -70% 0px' });
    ids.forEach(function (id) { var h = document.getElementById(id); if (h) observer.observe(h); });
  }

  /* ── 최근 본 문서 ── */
  function initRecent() {
    var list = core.parseRecent(storageGet(RECENT_KEY));
    list = list.filter(function (x) { return !!x && typeof x.repo === 'string' && typeof x.path === 'string'; });
    var doc = window.SYNCTX_DOC;
    if (doc && ACTIVE_REPO) {
      list = core.addRecent(list, { repo: ACTIVE_REPO, path: doc.path, ref: ACTIVE_REF, title: doc.title }, Date.now());
      storageSet(RECENT_KEY, JSON.stringify(list));
    }
    var shown = list.filter(function (x) {
      return !(doc && x.repo === ACTIVE_REPO && x.path === doc.path && (x.ref || '') === ACTIVE_REF);
    }).slice(0, 5);
    $all('[data-recent]').forEach(function (ul) {
      ul.innerHTML = shown.length ? shown.map(function (x) {
        var href = VIEW_PREFIX + '/' + encodeURIComponent(x.repo) + '/' + encodePath(x.path) + (x.ref ? '?ref=' + encodeURIComponent(x.ref) : '');
        return '<li><a href="' + escapeHtml(href) + '" title="' + escapeHtml(x.path) + '">' + escapeHtml(x.title || x.path) + '</a></li>';
      }).join('') : '<li class="recent-empty">아직 본 문서가 없습니다.</li>';
    });
  }

  /* ── Marp ── */
  function rawMarkdown() {
    try { return JSON.parse(document.getElementById('raw-markdown-json').textContent).markdown; } catch (e) { return ''; }
  }
  async function marpRequest(endpoint, body) {
    var res = await fetch('/marp/api/marp/' + endpoint, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(String(res.status));
    return res;
  }
  function fallbackToHtml() {
    var doc = window.SYNCTX_DOC;
    if (doc && doc.downloadHtml) window.location.href = doc.downloadHtml;
  }
  async function marpPdf() {
    var progress = synctxToast({ kind: 'progress', title: 'PDF 만드는 중…' });
    try {
      var blob = await (await marpRequest('convert', { markdown: rawMarkdown() })).blob();
      var url = URL.createObjectURL(blob);
      var a = document.createElement('a');
      a.href = url;
      a.download = (window.SYNCTX_DOC && window.SYNCTX_DOC.publishName || 'slides') + '.pdf';
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      progress.close();
      synctxToast({ kind: 'success', title: 'PDF를 내려받았습니다', timeout: 4000 });
    } catch (e) {
      progress.close();
      synctxToast({ kind: 'error', title: 'PDF를 만들지 못했습니다', body: 'Marp 서비스가 응답하지 않았습니다. HTML로 받아 보세요.', actionLabel: 'HTML로 받기', onAction: fallbackToHtml });
    }
  }
  async function marpPreview() {
    var win = window.open('', '_blank');
    var progress = synctxToast({ kind: 'progress', title: '슬라이드를 만드는 중…' });
    try {
      var html = await (await marpRequest('preview', { markdown: rawMarkdown() })).text();
      if (win) { win.document.open(); win.document.write(html); win.document.close(); }
      progress.close();
    } catch (e) {
      progress.close();
      if (win) win.close();
      synctxToast({ kind: 'error', title: '슬라이드를 만들지 못했습니다', body: 'Marp 서비스가 응답하지 않았습니다. 잠시 후 다시 시도하세요.' });
    }
  }
  function marpPublish() {
    var dialog = $('#publish-dialog');
    if (!dialog || typeof dialog.showModal !== 'function') return;
    dialog.returnValue = '';
    dialog.showModal();
    dialog.addEventListener('close', async function onClose() {
      dialog.removeEventListener('close', onClose);
      if (dialog.returnValue !== 'confirm') return;
      var progress = synctxToast({ kind: 'progress', title: '출판하는 중…' });
      try {
        var data = await (await marpRequest('publish', { markdown: rawMarkdown(), filename: window.SYNCTX_DOC.publishName })).json();
        progress.close();
        synctxToast({ kind: 'success', title: '출판했습니다', body: data.url, actionLabel: '열기', onAction: function () { window.open(data.url, '_blank', 'noopener'); } });
      } catch (e) {
        progress.close();
        synctxToast({ kind: 'error', title: '출판하지 못했습니다', body: '권한이나 게시 저장소 설정을 확인하세요.' });
      }
    });
  }

  /* ── 단축키 안내 ── */
  function openShortcuts() { var d = $('#shortcuts-dialog'); if (d && typeof d.showModal === 'function' && !d.open) d.showModal(); }

  function initClicks() {
    document.addEventListener('click', function (e) {
      var dir = e.target.closest('.tree-dir');
      if (dir) { toggleDir(dir); return; }
      var tab = e.target.closest('[data-nav-tab]');
      if (tab) { showNavTab(tab.dataset.navTab); return; }
      var anchor = e.target.closest('a[href^="#"]');
      if (anchor && navOpen() && anchor.closest('#shell-nav')) {
        // 서랍을 inert로 만들기 전에 링크의 기본 이동(해시 이동)이 끝나도록 한 틱 미룬다
        var heading = document.getElementById(decodeURIComponent(anchor.getAttribute('href').slice(1)));
        window.setTimeout(function () {
          setNav(false);
          if (heading) { heading.setAttribute('tabindex', '-1'); heading.focus({ preventScroll: true }); }
        }, 0);
        return;
      }
      var el = e.target.closest('[data-action]');
      var action = el && el.getAttribute('data-action');
      if (action === 'toggle-nav') setNav(!navOpen());
      else if (action === 'close-nav') setNav(false);
      else if (action === 'open-toc') setNav(true, 'toc');
      else if (action === 'shortcuts') openShortcuts();
      else if (action === 'marp-pdf') marpPdf();
      else if (action === 'marp-preview') marpPreview();
      else if (action === 'marp-publish') marpPublish();
      else if (action === 'reload') window.location.reload();
      $all('details.menu[open]').forEach(function (d) { if (!d.contains(e.target)) d.open = false; });
    });
    document.addEventListener('input', function (e) {
      if (e.target.matches && e.target.matches('[data-tree-filter]')) filterTree(e.target.value);
    });
  }

  function initKeys() {
    document.addEventListener('keydown', function (e) {
      var tag = e.target.tagName;
      var typing = tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || e.target.isContentEditable;
      if (e.key === 'Escape') {
        if (navOpen()) setNav(false);
        $all('details.menu[open]').forEach(function (d) { d.open = false; });
        return;
      }
      // 서랍이 열린 동안에는 서랍 밖(막힌 요소)을 여는 단축키를 쓰지 않는다
      if (navOpen()) return;
      if ((e.metaKey || e.ctrlKey) && String(e.key).toLowerCase() === 'k') { e.preventDefault(); focusFinder(); return; }
      if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === '/') { e.preventDefault(); focusFinder(); }
      else if (e.key === '?') openShortcuts();
      else if (e.key === 't' && $('.shell-toc')) setNav(true, 'toc');
    });
  }

  initSelects();
  initNav();
  initClicks();
  initKeys();
  initTree();
  initFinder();
  initTocSpy();
  initRecent();
})();
  loadBranches();

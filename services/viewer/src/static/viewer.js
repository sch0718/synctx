/* Viewer 화면 동작: 테마·저장소 이동, 서랍, 트리, 문서 검색, 목차 추적, 알림, Marp, 최근 문서, 단축키. */
(function () {
  'use strict';

  var core = window.SynctxViewerCore;
  var RECENT_KEY = 'synctx-recent';
  var THEME_KEY = 'synctx-theme';
  var MEETING_GUIDE_KEY = 'synctx-meeting-guide';
  var SUCCESS_TOAST_MS = 8000;

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
    var dismiss = document.createElement('button');
    dismiss.type = 'button';
    dismiss.className = 'btn toast-close';
    dismiss.textContent = '닫기';
    dismiss.setAttribute('aria-label', '닫기');
    dismiss.addEventListener('click', function () { close(); });
    el.appendChild(dismiss);
    region.appendChild(el);
    var timer = null;
    if (opts.kind === 'progress') {
      var started = Date.now();
      body.textContent = '0초 지남';
      timer = window.setInterval(function () {
        body.textContent = Math.round((Date.now() - started) / 1000) + '초 지남';
      }, 1000);
    } else {
      var timeout = opts.timeout || (opts.kind === 'success' ? SUCCESS_TOAST_MS : 0);
      if (timeout) timer = window.setTimeout(close, timeout);
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

  /* ── 서랍(767px 이하)과 탭(1099px 이하) ── */
  var DRAWER = window.matchMedia('(max-width: 767px)');
  var WIDE = window.matchMedia('(min-width: 1100px)');
  var blocked = [];

  function showNavTab(name) {
    $all('[data-nav-tab]').forEach(function (t) { t.setAttribute('aria-selected', String(t.dataset.navTab === name)); });
    $all('[data-nav-panel]').forEach(function (p) { p.hidden = p.dataset.navPanel !== name; });
    var panel = $('[data-nav-panel="toc"]');
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

  /* ── 문서 검색 ── */
  function initFinder() {
    var input = $('#finder-input');
    var list = $('#finder-results');
    if (!input || !list) return;
    var loader = core.createFileLoader(function () {
      return fetch(VIEW_PREFIX + '/api/repos/' + encodeURIComponent(ACTIVE_REPO) + '/files' + _refParam('?'))
        .then(function (res) { if (!res.ok) throw new Error('files'); return res.json(); })
        .then(function (data) { return data.files || []; });
    });
    var scheduler = core.createSearchScheduler({
      delay: 250,
      request: function (q, signal) {
        return fetch(VIEW_PREFIX + '/api/search?q=' + encodeURIComponent(q) +
          (ACTIVE_REPO ? '&repo=' + encodeURIComponent(ACTIVE_REPO) + _refParam('&') : ''), { signal: signal })
          .then(function (res) { if (!res.ok) throw new Error('search'); return res.json(); });
      },
      onResult: function (q, data) {
        // 기다리는 동안 사용자가 닫았거나 다른 곳으로 옮겼으면 다시 열지 않는다
        if (dismissed || document.activeElement !== input || !input.value.trim()) return;
        if (input.value.trim() !== q) return;
        searchData = data;
        searchQuery = q;
        refresh();
      },
    });
    var items = [];
    var active = -1;
    var dismissed = false;
    var moved = false;
    var loadFailed = false;
    var fileMatches = [];
    var searchData = null;
    var searchQuery = '';

    function setOpen(open) { list.hidden = !open; input.setAttribute('aria-expanded', String(open)); }
    function clearResults() {
      scheduler.cancel();
      items = []; active = -1; fileMatches = []; searchData = null; searchQuery = '';
      input.setAttribute('aria-activedescendant', '');
      setOpen(false);
    }
    function message(text) { items = []; active = -1; list.innerHTML = '<li class="finder-empty">' + escapeHtml(text) + '</li>'; setOpen(true); }
    function optionKey(o) { return [o.kind, o.repo, o.path, o.line].join('|'); }
    function optionHtml(o, i) {
      var head = '<li role="option" id="finder-opt-' + i + '" aria-selected="' + (i === active) + '" data-index="' + i + '">';
      if (o.kind === 'all') {
        return head + '<span class="finder-name">‘' + escapeHtml(input.value.trim()) + '’ 전체 결과 보기</span></li>';
      }
      if (o.kind === 'file') {
        return head + '<span class="finder-name">' + escapeHtml(o.path.split('/').pop()) + '</span>' +
          '<span class="finder-path">' + escapeHtml(o.path) + '</span></li>';
      }
      return head + '<span class="finder-name">' + escapeHtml(o.path.split('/').pop()) + '</span>' +
        '<span class="finder-path">' + escapeHtml((ACTIVE_REPO ? '' : o.repoName + ' · ') + o.path + ' · ' + o.line + '행') + '</span>' +
        '<span class="finder-snippet">' + escapeHtml(o.before) + (o.match ? '<mark>' + escapeHtml(o.match) + '</mark>' : '') + escapeHtml(o.after) + '</span></li>';
    }
    function paint() {
      var status = core.finderFileStatus(ACTIVE_REPO, ACTIVE_REPO ? loader.files() : [], loadFailed);
      var html = status ? '<li role="presentation" class="finder-empty">' + escapeHtml(status) + '</li>' : '';
      var group = null;
      var groupCount = 0;
      items.forEach(function (o, i) {
        if (o.group !== group) {
          if (group) html += '</ul></li>';
          if (o.group) {
            var groupId = 'finder-group-' + (groupCount++);
            html += '<li role="group" aria-labelledby="' + groupId + '"><span class="finder-group" id="' + groupId + '">' +
              escapeHtml(o.group) + '</span><ul role="none">';
          }
          group = o.group;
        }
        html += optionHtml(o, i);
      });
      if (group) html += '</ul></li>';
      list.innerHTML = html;
      input.setAttribute('aria-activedescendant', active >= 0 ? 'finder-opt-' + active : '');
      setOpen(true);
      // 포커스는 입력칸에 있으므로 선택한 결과가 목록 밖이면 직접 스크롤한다
      var current = active >= 0 && document.getElementById('finder-opt-' + active);
      if (current) current.scrollIntoView({ block: 'nearest' });
    }
    function refresh() {
      var q = input.value.trim();
      var kept = moved && active >= 0 && items[active] ? optionKey(items[active]) : null;
      items = core.finderOptions(fileMatches, searchQuery === q ? searchData : null, q);
      active = items.length ? 0 : -1;
      if (kept) {
        for (var i = 0; i < items.length; i++) { if (optionKey(items[i]) === kept) { active = i; break; } }
      }
      if (!items.length) {
        message(core.finderFileStatus(ACTIVE_REPO, ACTIVE_REPO ? loader.files() : [], loadFailed) ||
          (ACTIVE_REPO ? '맞는 파일이 없습니다.' : '두 글자 이상 입력하세요.'));
        return;
      }
      paint();
    }
    function showFiles() {
      var files = ACTIVE_REPO ? loader.files() : [];
      if (files === null) {
        fileMatches = [];
        if (!loadFailed) {
          loader.load().then(function (result) {
            // 기다리는 동안 사용자가 닫았거나 다른 곳으로 옮겼으면 다시 열지 않는다
            if (dismissed || document.activeElement !== input || !input.value.trim()) return;
            if (!result.ok) loadFailed = true;
            showFiles();
          });
        }
      } else {
        fileMatches = core.matchFiles(files, input.value, 8);
      }
      refresh();
    }
    function render() {
      if (!input.value.trim()) { clearResults(); return; }
      var q = input.value.trim();
      moved = false;
      if (core.isSearchable(q)) scheduler.schedule(q); else scheduler.cancel();
      showFiles();
    }
    function go(option) {
      window.location.href = core.finderHref(option, {
        viewPrefix: VIEW_PREFIX, activeRepo: ACTIVE_REPO, activeRef: ACTIVE_REF, query: input.value.trim(),
      });
    }
    input.addEventListener('input', function () { dismissed = false; loadFailed = false; render(); });
    input.addEventListener('focus', function () { if (ACTIVE_REPO) loader.load(); });
    input.addEventListener('keydown', function (e) {
      // 한글 조합을 끝내는 Enter·방향키는 검색 결과 조작이 아니다
      if (e.isComposing || e.keyCode === 229) return;
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        if (!items.length) return;
        e.preventDefault();
        moved = true;
        active = (active + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
        paint();
      } else if (e.key === 'Enter') {
        if (active >= 0 && !list.hidden) { e.preventDefault(); go(items[active]); }
      } else if (e.key === 'Escape') {
        dismissed = true;
        scheduler.cancel();
        setOpen(false);
      }
    });
    list.addEventListener('mousedown', function (e) {
      var li = e.target.closest('li[data-index]');
      if (li) { e.preventDefault(); go(items[Number(li.dataset.index)]); }
    });
    input.addEventListener('blur', function () {
      scheduler.cancel();
      window.setTimeout(function () { setOpen(false); }, 150);
    });
  }
  function focusFinder() { var input = $('#finder-input'); if (input) { input.focus(); input.select(); } }

  /* ── 이 문서 목차·안건: 보이는 절 표시 ── */
  function initTocSpy() {
    var source = $all('.shell-toc .toc a[href^="#"]');
    var agenda = $all('.agenda a[href^="#"]');
    if ((!source.length && !agenda.length) || !('IntersectionObserver' in window)) return;
    var ids = source.map(function (a) { return decodeURIComponent(a.getAttribute('href').slice(1)); });
    var headings = $all('.markdown-body h1, .markdown-body h2, .markdown-body h3, .markdown-body h4').filter(function (h) { return !!h.id; });
    var owners = core.sectionOwners(headings.map(function (h) { return { id: h.id, level: Number(h.tagName.slice(1)) }; }));
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        if (ids.indexOf(entry.target.id) !== -1) {
          // 원본 목차와 서랍에 복제한 목차를 함께 갱신한다
          $all('.toc a[href^="#"]').forEach(function (a) {
            if (decodeURIComponent(a.getAttribute('href').slice(1)) === entry.target.id) a.setAttribute('aria-current', 'location');
            else a.removeAttribute('aria-current');
          });
        }
        var owner = owners[entry.target.id];
        $all('.agenda a[href^="#"]').forEach(function (a) {
          if (owner && decodeURIComponent(a.getAttribute('href').slice(1)) === owner) a.setAttribute('aria-current', 'location');
          else a.removeAttribute('aria-current');
        });
      });
    }, { rootMargin: '-64px 0px -70% 0px' });
    ids.forEach(function (id) { var h = document.getElementById(id); if (h) observer.observe(h); });
    if (agenda.length) headings.forEach(function (h) { if (ids.indexOf(h.id) === -1) observer.observe(h); });
  }

  /* ── 회의 정보 카드: 진행 상태 ── */
  function initMeetingStatus() {
    var card = $('.meeting-info');
    var label = card && $('.meeting-info-status', card);
    if (!label) return;
    var startsAt = Date.parse(card.dataset.startsAt);
    var duration = Number(card.dataset.duration);
    var cancelled = card.dataset.cancelled === 'true';
    function update() {
      var status = core.meetingStatus(startsAt, duration, Date.now(), cancelled);
      if (status && label.textContent !== status.label) label.textContent = status.label;
    }
    update();
    window.setInterval(update, 30000);
  }

  /* ── 발표 화면: h2마다 장을 나눠 숨김만 바꾼다(회의 모드 형광펜이 본문 글자 위치에 붙는다) ── */
  function initPresent() {
    if (!document.body.classList.contains('page-present')) return;
    var article = $('article.markdown-body');
    if (!article) return;
    // 원문에서 이미 숨긴 요소는 장에 넣지 않아 발표 중에도 계속 숨는다
    var blocks = Array.prototype.filter.call(article.children, function (el) { return !el.hidden && !el.hasAttribute('data-meeting-ui'); });
    var slideOf = core.buildSlides(blocks.map(function (el) { return el.tagName; }));
    var total = blocks.length ? slideOf[slideOf.length - 1] + 1 : 0;
    var titles = [];
    blocks.forEach(function (el, i) {
      if (titles[slideOf[i]] === undefined && (el.tagName === 'H1' || el.tagName === 'H2')) titles[slideOf[i]] = el.textContent.trim();
    });
    var count = $('.present-count');
    var progress = $('.present-progress');
    var bar = $('.present-progress-bar');
    var live = $('[data-present-live]');
    var prev = $('[data-action="present-prev"]');
    var next = $('[data-action="present-next"]');
    var exit = $('[data-present-exit]');
    var current = -1;

    function blockIndexOf(el) {
      var top = el;
      while (top && top.parentElement !== article) top = top.parentElement;
      return blocks.indexOf(top);
    }
    // 숨긴 장에서 그리면 그림이 0 크기로 굳으므로 보이는 장의 그림만, 아직 안 그린 것만 그린다
    function renderMermaid(n) {
      if (!window.mermaid) return;
      var nodes = [];
      blocks.forEach(function (el, i) {
        if (slideOf[i] !== n) return;
        if (el.matches('.mermaid')) nodes.push(el);
        nodes.push.apply(nodes, $all('.mermaid', el));
      });
      nodes = nodes.filter(function (el) { return !el.hasAttribute('data-processed'); });
      if (nodes.length) window.mermaid.run({ nodes: nodes });
    }
    function show(n, announce, keepHash) {
      current = n;
      blocks.forEach(function (el, i) { el.hidden = slideOf[i] !== n; });
      renderMermaid(n);
      var label = (n + 1) + ' / ' + total;
      if (count) count.textContent = label;
      if (progress) {
        progress.setAttribute('aria-valuemax', String(total));
        progress.setAttribute('aria-valuenow', String(n + 1));
        progress.setAttribute('aria-valuetext', label + '장');
      }
      if (bar) bar.style.width = ((n + 1) / total * 100) + '%';
      if (!keepHash) history.replaceState(history.state, '', '#slide-' + (n + 1));
      if (announce && live) live.textContent = label + '장' + (titles[n] ? ': ' + titles[n] : '');
    }
    function go(n) {
      var target = Math.min(Math.max(n, 0), total - 1);
      if (target === current) return;
      show(target, true);
      window.scrollTo(0, 0);
    }
    function toggleMemo() {
      var toggle = document.querySelector('.meeting-toggle');
      if (toggle) toggle.click();
    }
    function run(action) {
      if (action === 'next') go(current + 1);
      else if (action === 'prev') go(current - 1);
      else if (action === 'first') go(0);
      else if (action === 'last') go(total - 1);
      else if (action === 'memo') toggleMemo();
      else if (action === 'exit' && exit) window.location.href = exit.href;
    }
    function updateTime() {
      var card = $('.meeting-info');
      var time = $('[data-present-time]');
      if (!card || !time) return;
      var status = core.meetingStatus(Date.parse(card.dataset.startsAt), Number(card.dataset.duration), Date.now(),
        card.dataset.cancelled === 'true');
      var text = core.presentTimeLabel(status);
      if (time.textContent !== text) time.textContent = text;
    }
    // #slide-N은 그 장, 본문 안 id는 그 요소가 든 장으로 옮기고 주소는 그대로 둔다. 본문 밖 id(본문으로 건너뛰기 등)는 브라우저에 맡긴다
    function followHash(initial) {
      var hash = window.location.hash;
      var el = null;
      if (hash.length > 1 && !/^#slide-\d+$/.test(hash)) {
        var id = hash.slice(1);
        try { id = decodeURIComponent(id); } catch (e) { id = hash.slice(1); }
        el = document.getElementById(id);
      }
      var i = el && article.contains(el) ? blockIndexOf(el) : -1;
      if (i !== -1) {
        if (slideOf[i] !== current) show(slideOf[i], !initial, true);
        el.scrollIntoView();
        return;
      }
      if (el && !initial) return;
      var n = core.slideFromHash(hash, total);
      if (initial || n === current) show(n, false);
      else go(n);
    }

    if (total) {
      followHash(true);
      window.addEventListener('hashchange', function () { followHash(false); });
      // mermaid는 viewer.js 뒤에 읽히므로 첫 장 그림은 문서 해석이 끝난 뒤 그린다
      if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () { renderMermaid(current); });
      }
    } else {
      if (count) count.textContent = '0 / 0';
      if (progress) progress.hidden = true;
      if (prev) prev.disabled = true;
      if (next) next.disabled = true;
      var empty = $('.present-empty');
      if (empty) empty.hidden = false;
    }
    updateTime();
    window.setInterval(updateTime, 30000);

    var nav = $('.present-nav');
    if (nav) {
      nav.addEventListener('click', function (e) {
        var el = e.target.closest('[data-action]');
        var action = el && el.getAttribute('data-action');
        if (action === 'present-prev') run('prev');
        else if (action === 'present-next') run('next');
        else if (action === 'present-memo') run('memo');
      });
    }
    // 캡처 단계에서 먼저 판단해, 패널이 닫힌 때의 Esc가 회의 모드의 패널 닫기와 겹치지 않게 한다
    window.addEventListener('keydown', function (e) {
      if (e.isComposing || e.keyCode === 229) return;
      var target = e.target && e.target.closest ? e.target : document.body;
      var tag = target.tagName;
      var panel = document.querySelector('.meeting-panel');
      var action = core.presentKeyAction(e.key, {
        shiftKey: e.shiftKey,
        modKey: e.metaKey || e.ctrlKey || e.altKey,
        typing: tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target.isContentEditable || !!target.closest('.meeting-panel'),
        onControl: !!target.closest('button, a[href], summary, [role="button"]'),
        panelOpen: !!panel && !panel.hidden,
      });
      if (!action || (!total && action !== 'memo' && action !== 'exit')) return;
      e.preventDefault();
      e.stopPropagation();
      run(action);
    }, true);

    // 회의 모드가 숨긴 장의 형광펜으로 가려 하면(meeting-hl--flash가 붙는다) 그 장을 보이고 다시 옮긴다
    if (total && 'MutationObserver' in window) {
      new MutationObserver(function (records) {
        var id = null;
        records.forEach(function (r) {
          // 깜빡이는 중에 같은 메모로 다시 가도(표시가 이미 붙어 있어도) 그 장으로 옮긴다
          if (r.target.matches('mark.meeting-hl.meeting-hl--flash[data-memo-id]')) id = r.target.getAttribute('data-memo-id');
        });
        if (id === null) return;
        var safeId = window.CSS && CSS.escape ? CSS.escape(id) : id;
        var mark = article.querySelectorAll('mark.meeting-hl[data-memo-id="' + safeId + '"]')[0];
        var i = blockIndexOf(mark);
        if (i === -1 || slideOf[i] === current) return;
        show(slideOf[i], true);
        mark.scrollIntoView({ block: 'center' });
      }).observe(article, { attributes: true, attributeFilter: ['class'], subtree: true });
    }

    // 메모 패널은 위·아래 띠 사이에 둔다. 띠는 좁은 화면에서 줄이 바뀌므로 높이를 다시 잰다
    var topBand = $('.present-top');
    function measureBands() {
      var rootStyle = document.documentElement.style;
      if (topBand) rootStyle.setProperty('--present-top-h', topBand.offsetHeight + 'px');
      if (nav) rootStyle.setProperty('--present-nav-h', nav.offsetHeight + 'px');
    }
    measureBands();
    if ('ResizeObserver' in window) {
      var bandObserver = new ResizeObserver(measureBands);
      if (topBand) bandObserver.observe(topBand);
      if (nav) bandObserver.observe(nav);
    } else {
      window.addEventListener('resize', measureBands);
    }

    // "메모 보기(N)"가 회의 모드 메모 버튼과 같은 열림 상태를 알린다. 메모 버튼은 meeting.js가 문서 해석 뒤에 만든다
    var memoButton = $('[data-action="present-memo"]');
    function watchMemoToggle() {
      var toggle = document.querySelector('.meeting-toggle');
      if (!memoButton || !toggle) return;
      function sync() { memoButton.setAttribute('aria-expanded', toggle.getAttribute('aria-expanded') === 'true' ? 'true' : 'false'); }
      sync();
      if ('MutationObserver' in window) new MutationObserver(sync).observe(toggle, { attributes: true, attributeFilter: ['aria-expanded'] });
    }
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', function () { window.setTimeout(watchMemoToggle, 0); });
    } else {
      watchMemoToggle();
    }
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
  function marpFailure(e, serviceDownBody) {
    if (e && e.message === '403') return { title: '권한이 없습니다', body: '슬라이드를 만들거나 출판할 권한이 필요합니다. 관리자에게 요청하세요.' };
    return { body: serviceDownBody };
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
      var failure = marpFailure(e, 'Marp 서비스가 응답하지 않았습니다. HTML로 받아 보세요.');
      synctxToast({ kind: 'error', title: failure.title || 'PDF를 만들지 못했습니다', body: failure.body, actionLabel: 'HTML로 받기', onAction: fallbackToHtml });
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
      var failure = marpFailure(e, 'Marp 서비스가 응답하지 않았습니다. 잠시 후 다시 시도하세요.');
      synctxToast({ kind: 'error', title: failure.title || '슬라이드를 만들지 못했습니다', body: failure.body });
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
        synctxToast({ kind: 'success', title: '출판했습니다', body: data.url, timeout: 20000, actionLabel: '열기', onAction: function () { window.open(data.url, '_blank', 'noopener'); } });
      } catch (e) {
        progress.close();
        var failure = marpFailure(e, '권한이나 게시 저장소 설정을 확인하세요.');
        synctxToast({ kind: 'error', title: failure.title || '출판하지 못했습니다', body: failure.body });
      }
    });
  }

  /* ── 단축키 안내 ── */
  function openShortcuts() { var d = $('#shortcuts-dialog'); if (d && typeof d.showModal === 'function' && !d.open) d.showModal(); }

  /* ── 회의 모드 안내 ── */
  function initMeetingGuide() {
    var guide = $('#meeting-guide');
    if (!guide) return;
    $all('[data-action="meeting-guide-open"]').forEach(function (b) { b.hidden = false; });
    if (core.meetingGuideAutoOpen(storageGet(MEETING_GUIDE_KEY))) guide.hidden = false;
  }

  function openMeetingGuide() {
    var guide = $('#meeting-guide');
    if (!guide) return;
    guide.hidden = false;
    guide.scrollIntoView({ block: 'nearest' });
    var title = $('#meeting-guide-title');
    if (title) title.focus({ preventScroll: true });
  }

  function closeMeetingGuide(never) {
    var guide = $('#meeting-guide');
    if (!guide) return;
    if (never) storageSet(MEETING_GUIDE_KEY, core.MEETING_GUIDE_HIDDEN);
    guide.hidden = true;
    var opener = $('.meeting-guide-open');
    if (opener) opener.focus();
  }

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
      else if (action === 'meeting-guide-open') {
        var dialog = el.closest('#shortcuts-dialog');
        if (dialog) { dialog.close(); window.setTimeout(openMeetingGuide, 0); }
        else openMeetingGuide();
      }
      else if (action === 'meeting-guide-close') closeMeetingGuide(false);
      else if (action === 'meeting-guide-never') closeMeetingGuide(true);
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
      // 발표 화면은 서랍·찾기·목차를 숨기므로 그것을 여는 단축키를 쓰지 않는다
      if (document.body.classList.contains('page-present')) return;
      var tag = e.target.tagName;
      var typing = tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || e.target.isContentEditable;
      if (e.key === 'Escape') {
        if (navOpen()) setNav(false);
        $all('details.menu[open]').forEach(function (d) { d.open = false; });
        var guide = $('#meeting-guide');
        if (guide && !guide.hidden && guide.contains(document.activeElement)) closeMeetingGuide(false);
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
  loadBranches();
  initNav();
  initClicks();
  initKeys();
  initTree();
  initFinder();
  initTocSpy();
  initMeetingStatus();
  initMeetingGuide();
  initPresent();
  initRecent();
})();

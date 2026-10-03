/* synctx 회의 모드 화면: 메모 버튼, 형광펜, 패널, 저장. memo-core.js 다음에 실행된다. */
(function () {
  'use strict';

  var core = (typeof globalThis !== 'undefined' ? globalThis : window).SynctxMemoCore;
  var UI_ATTR = 'data-meeting-ui';
  var EMBED_MARK = 'synctx-' + 'meeting-embed';
  var KIND_LABEL = { note: '일반', decision: '결정', action: '액션' };
  var BLOCKED_PARENTS = { TR: true, TBODY: true, THEAD: true, TFOOT: true, TABLE: true, UL: true, OL: true };
  var SAVE_DELAY_MS = 300;
  var MIN_PANEL_WIDTH = 300;

  var cfg, root, embed, readonly, storage, persistent;
  var memoPrefix, docKey, widthKey;
  var chosenWidth = 400;
  var revivedId = null;
  var state = { memos: {}, lastWritten: {}, drafts: {}, timers: {}, conflicts: {} };
  var ui = { panel: null, list: null, summary: null, status: null, notice: null, toggle: null, resizer: null, selBtn: null, cards: {}, tab: 'memos', pendingSelection: null };

  function h(tag, props, children) {
    var node = document.createElement(tag);
    Object.keys(props || {}).forEach(function (key) {
      var value = props[key];
      if (value == null || value === false) return;
      if (key === 'class') node.className = value;
      else if (key.slice(0, 2) === 'on') node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? '' : value);
    });
    (children || []).forEach(function (child) {
      if (child == null) return;
      node.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
    });
    return node;
  }

  function prevent(e) {
    e.preventDefault();
  }

  function readConfig() {
    var el = document.getElementById('meeting-config');
    if (!el) return null;
    try {
      return JSON.parse(el.textContent);
    } catch (e) {
      return null;
    }
  }

  function memoryStorage() {
    var map = new Map();
    return {
      getItem: function (k) { return map.has(k) ? map.get(k) : null; },
      setItem: function (k, v) { map.set(k, String(v)); },
      removeItem: function (k) { map.delete(k); },
      key: function (i) { return Array.from(map.keys())[i] || null; },
      get length() { return map.size; }
    };
  }

  function openStorage() {
    try {
      var s = window.localStorage;
      var probe = '__synctx_meeting_probe__';
      s.setItem(probe, '1');
      s.removeItem(probe);
      return { storage: s, persistent: true };
    } catch (e) {
      return { storage: memoryStorage(), persistent: false };
    }
  }

  function docInfo() {
    return { repo_id: cfg.repo_id, meeting_id: cfg.meeting.id, path: cfg.path, title: cfg.meeting.title };
  }

  function allMemos() {
    return Object.keys(state.memos).map(function (id) { return state.memos[id]; });
  }

  function liveMemos() {
    return allMemos().filter(function (m) { return !m.deleted_at; });
  }

  function positions() {
    var out = {};
    allMemos().forEach(function (m) {
      if (m._loc && !m._loc.lost) out[m.id] = m._loc.start;
    });
    return out;
  }

  // ── 본문 텍스트 색인: 회의 UI와 Mermaid는 세지 않는다(설계 3.8) ──

  function buildIndex() {
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: function (n) {
        var parent = n.parentElement;
        return parent && parent.closest('[' + UI_ATTR + '], .mermaid') ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT;
      }
    });
    var entries = [];
    var text = '';
    var node;
    while ((node = walker.nextNode())) {
      entries.push({ node: node, start: text.length, end: text.length + node.data.length });
      text += node.data;
    }
    return { text: text, entries: entries };
  }

  function pointToOffset(index, container, offset) {
    if (container.nodeType === Node.TEXT_NODE) {
      for (var i = 0; i < index.entries.length; i++) {
        if (index.entries[i].node === container) return index.entries[i].start + offset;
      }
    }
    var point = document.createRange();
    try {
      point.setStart(container, offset);
    } catch (e) {
      return null;
    }
    for (var j = 0; j < index.entries.length; j++) {
      if (point.comparePoint(index.entries[j].node, 0) >= 0) return index.entries[j].start;
    }
    return index.text.length;
  }

  function rangeToOffsets(index, range) {
    var a = pointToOffset(index, range.startContainer, range.startOffset);
    var b = pointToOffset(index, range.endContainer, range.endOffset);
    if (a == null || b == null) return null;
    return { start: Math.min(a, b), end: Math.max(a, b) };
  }

  function lastVisibleChar(index, off) {
    for (var i = index.entries.length - 1; i >= 0; i--) {
      var e = index.entries[i];
      if (e.start >= off.end || e.end <= off.start) continue;
      var parent = e.node.parentNode;
      if (!parent || BLOCKED_PARENTS[parent.nodeName]) continue;
      for (var pos = Math.min(off.end, e.end) - 1; pos >= Math.max(off.start, e.start); pos--) {
        if (/\S/.test(index.text.charAt(pos))) return { node: e.node, offset: pos - e.start };
      }
    }
    return null;
  }

  // ── 형광펜 ──

  function clearHighlights() {
    var marks = root.querySelectorAll('mark.meeting-hl');
    for (var i = 0; i < marks.length; i++) {
      var mark = marks[i];
      var parent = mark.parentNode;
      while (mark.firstChild) parent.insertBefore(mark.firstChild, mark);
      parent.removeChild(mark);
    }
    if (marks.length) root.normalize();
  }

  function highlight(memo) {
    var index = buildIndex();
    var start = memo._loc.start;
    var end = memo._loc.end;
    var pieces = [];
    index.entries.forEach(function (e) {
      var s = Math.max(start, e.start);
      var t = Math.min(end, e.end);
      if (s >= t) return;
      var parent = e.node.parentNode;
      if (!parent || BLOCKED_PARENTS[parent.nodeName] || (parent.closest && parent.closest('svg'))) return;
      if (!e.node.data.slice(s - e.start, t - e.start).trim()) return;
      pieces.push({ node: e.node, from: s - e.start, to: t - e.start });
    });
    pieces.forEach(function (p) {
      var node = p.node;
      if (p.to < node.data.length) node.splitText(p.to);
      var target = p.from > 0 ? node.splitText(p.from) : node;
      var mark = h('mark', {
        class: 'meeting-hl meeting-hl--' + memo.kind,
        'data-memo-id': memo.id,
        title: KIND_LABEL[memo.kind] + ' 메모',
        onclick: function (e) { e.stopPropagation(); openPanel('memos'); focusCard(memo.id); }
      });
      target.parentNode.insertBefore(mark, target);
      mark.appendChild(target);
    });
  }

  function relocate() {
    clearHighlights();
    var index = buildIndex();
    allMemos().forEach(function (m) {
      m._loc = m.anchor ? core.locate(index.text, m.anchor) : null;
    });
    liveMemos().forEach(function (m) {
      if (m._loc && !m._loc.lost && m._loc.end > m._loc.start) highlight(m);
    });
  }

  // ── 메모 만들기 ──

  function addBlockButtons() {
    var blocks = root.querySelectorAll('h2, h3, h4, tr');
    Array.prototype.forEach.call(blocks, function (block) {
      if (block.closest('[' + UI_ATTR + '], .mermaid')) return;
      var host = block.tagName === 'TR' ? block.lastElementChild : block;
      if (!host) return;
      host.appendChild(h('button', {
        type: 'button', class: 'meeting-add', 'data-meeting-ui': '', 'aria-label': '이 부분에 메모 추가',
        onmousedown: prevent, onclick: function () { createFromBlock(block); }
      }, ['+ 메모']));
    });
    root.insertBefore(h('div', { class: 'meeting-doc-actions', 'data-meeting-ui': '' }, [
      h('button', { type: 'button', class: 'meeting-add', onclick: function () { createMemo(null); } }, ['+ 전체 메모'])
    ]), root.firstChild);
  }

  function createFromBlock(block) {
    var index = buildIndex();
    var range = document.createRange();
    range.selectNodeContents(block);
    var off = rangeToOffsets(index, range);
    if (!off || off.end <= off.start) return;
    createMemo(core.makeAnchor(index.text, off.start, off.end));
  }

  function setupSelection() {
    ui.selBtn = h('button', {
      type: 'button', class: 'meeting-sel-btn', 'data-meeting-ui': '', hidden: true,
      onmousedown: prevent, onclick: createFromSelection
    }, ['+ 선택 영역에 메모']);
    document.body.appendChild(ui.selBtn);
    document.addEventListener('mouseup', function () { setTimeout(updateSelectionButton, 0); });
    document.addEventListener('keyup', function (e) {
      if (e.shiftKey || e.key === 'Shift') updateSelectionButton();
    });
  }

  function updateSelectionButton() {
    ui.pendingSelection = null;
    ui.selBtn.hidden = true;
    var sel = window.getSelection();
    if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return;
    var range = sel.getRangeAt(0);
    if (!root.contains(range.commonAncestorContainer)) return;
    var index = buildIndex();
    var off = rangeToOffsets(index, range);
    if (!off || off.end <= off.start || !index.text.slice(off.start, off.end).trim()) return;
    // 선택 끝이 칸 사이의 줄바꿈 노드에 걸리면 좌표가 0이 되므로, 보이는 마지막 글자 하나로 위치를 잡는다
    var point = lastVisibleChar(index, off);
    var rect = null;
    if (point) {
      var last = document.createRange();
      last.setStart(point.node, point.offset);
      last.setEnd(point.node, point.offset + 1);
      rect = last.getBoundingClientRect();
    }
    if (!rect || (rect.width === 0 && rect.height === 0)) rect = range.getBoundingClientRect();
    ui.pendingSelection = off;
    ui.selBtn.style.top = (window.scrollY + rect.bottom + 6) + 'px';
    ui.selBtn.style.left = (window.scrollX + rect.left) + 'px';
    ui.selBtn.hidden = false;
  }

  function createFromSelection() {
    var off = ui.pendingSelection;
    if (!off) return;
    var index = buildIndex();
    createMemo(core.makeAnchor(index.text, off.start, off.end));
    var sel = window.getSelection();
    if (sel) sel.removeAllRanges();
    ui.selBtn.hidden = true;
    ui.pendingSelection = null;
  }

  function createMemo(anchor) {
    var now = core.nowIso();
    var memo = { id: core.newId(), kind: 'note', anchor: anchor, text: '', owner: '', due: '', created_at: now, updated_at: now };
    persist(memo);
    refresh();
    openPanel('memos');
    focusCard(memo.id);
  }

  // ── 저장(메모별 키, 설계 3.6) ──

  function applyResult(id, r) {
    if (r.removed) {
      delete state.memos[id];
      delete state.lastWritten[id];
      return;
    }
    if (core.validateMemo(r.display) !== null) {
      delete state.memos[id];
      delete state.lastWritten[id];
      return;
    }
    state.memos[id] = r.display;
    if (r.lastWritten) state.lastWritten[id] = r.lastWritten;
    else delete state.lastWritten[id];
  }

  function persist(memo) {
    try {
      var r = core.writeMemo(storage, memoPrefix + memo.id, memo, state.lastWritten[memo.id] || null);
      applyResult(memo.id, r);
      if (!r.written && !r.removed) {
        setStatus('다른 탭이나 가져온 파일의 더 최신 내용이 있어 저장하지 않았습니다', true);
        if (ui.cards[memo.id] && state.memos[memo.id]) fillCard(ui.cards[memo.id], state.memos[memo.id]);
      }
    } catch (e) {
      state.memos[memo.id] = memo;
      setStatus('메모를 저장하지 못했습니다(저장 공간 부족 등). 화면에만 남아 있습니다.', true);
    }
  }

  function isEditing(id) {
    var card = ui.cards[id];
    return !!state.timers[id] || !!(card && card.contains(document.activeElement));
  }

  function readCardFields(card) {
    return {
      kind: card.querySelector('[data-field="kind"]').value,
      text: card.querySelector('[data-field="text"]').value,
      owner: card.querySelector('[data-field="owner"]').value,
      due: card.querySelector('[data-field="due"]').value
    };
  }

  function onEdit(id, card) {
    state.drafts[id] = readCardFields(card);
    clearTimeout(state.timers[id]);
    state.timers[id] = setTimeout(function () { commit(id); }, SAVE_DELAY_MS);
    updateCounter(card);
    card.querySelector('[data-field="action-row"]').hidden = state.drafts[id].kind !== 'action';
  }

  function sanitizeFields(fields, previous) {
    var out = Object.assign({}, fields);
    out.text = String(out.text || '').slice(0, core.LIMITS.text);
    out.owner = String(out.owner || '').slice(0, core.LIMITS.owner);
    if (out.due && !/^\d{4}-\d{2}-\d{2}$/.test(out.due)) {
      out.due = (previous && previous.due) || '';
      setStatus('기한 형식이 올바르지 않습니다(YYYY-MM-DD)', true);
    }
    return out;
  }

  function commit(id) {
    clearTimeout(state.timers[id]);
    delete state.timers[id];
    var fields = state.drafts[id];
    var base = state.memos[id];
    if (!fields || !base) return;
    delete state.drafts[id];
    if (state.conflicts[id] === 'changed') delete state.conflicts[id];
    persist(Object.assign(core.stripRuntime(base), sanitizeFields(fields, base), { updated_at: core.nextUpdatedAt(base) }));
    refresh({ keepCard: id });
  }

  function reviveAsNew(old, fields) {
    var now = core.nowIso();
    var memo = Object.assign(core.stripRuntime(old), fields ? sanitizeFields(fields, old) : {}, { id: core.newId(), created_at: now, updated_at: now });
    delete memo.deleted_at;
    persist(memo);
    return memo.id;
  }

  function resolveConflictOnBlur(id) {
    var conflict = state.conflicts[id];
    if (!conflict) return;
    delete state.conflicts[id];
    var m = state.memos[id];
    if (conflict === 'deleted' && m && m.deleted_at && window.confirm('다른 탭에서 지운 메모입니다. 다시 살릴까요?')) {
      var revived = core.stripRuntime(m);
      delete revived.deleted_at;
      revived.updated_at = core.nextUpdatedAt(m);
      persist(revived);
    }
    refresh();
  }

  function deleteMemo(id) {
    var m = state.memos[id];
    if (!m || !window.confirm('이 메모를 삭제할까요?')) return;
    clearTimeout(state.timers[id]);
    delete state.timers[id];
    delete state.drafts[id];
    delete state.conflicts[id];
    var now = core.nowIso();
    persist(Object.assign(core.stripRuntime(m), { deleted_at: now, updated_at: core.nextUpdatedAt(m, now) }));
    refresh();
  }

  function reconcileFromStorage(id) {
    var before = state.memos[id];
    var editing = isEditing(id);
    var fields = state.drafts[id] || (editing && ui.cards[id] ? readCardFields(ui.cards[id]) : null);
    var r = core.reconcileMemo(storage, memoPrefix + id, state.lastWritten[id] || null);
    applyResult(id, r);
    if (r.removed && editing && before) {
      clearTimeout(state.timers[id]);
      delete state.timers[id];
      delete state.drafts[id];
      if (window.confirm('다른 탭에서 정리된 메모입니다. 새 메모로 다시 저장할까요?')) revivedId = reviveAsNew(before, fields);
    } else if (!r.removed && editing && before && r.display.updated_at !== before.updated_at) {
      state.conflicts[id] = r.display.deleted_at ? 'deleted' : 'changed';
    }
    return editing && !r.removed;
  }

  function onStorage(e) {
    if (e.storageArea !== window.localStorage) return;
    var keep = null;
    revivedId = null;
    if (e.key === null) {
      // localStorage.clear(): key가 null로 온다. 알고 있던 메모를 모두 다시 확인한다
      Object.keys(state.memos).forEach(function (id) {
        if (reconcileFromStorage(id)) keep = id;
      });
    } else if (e.key.indexOf(memoPrefix) === 0) {
      var id = e.key.slice(memoPrefix.length);
      if (reconcileFromStorage(id)) keep = id;
    } else {
      return;
    }
    refresh({ keepCard: keep });
    if (revivedId) focusCard(revivedId);
  }

  // ── 패널 ──

  function setStatus(text, isError) {
    ui.status.textContent = text || '';
    ui.status.className = 'meeting-status' + (isError ? ' meeting-status--error' : '');
  }

  function showNotice(text) {
    ui.notice.textContent = text;
    ui.notice.hidden = false;
  }

  function clampWidth(w) {
    var max = Math.max(MIN_PANEL_WIDTH, Math.floor(window.innerWidth * 0.9));
    return Math.min(max, Math.max(MIN_PANEL_WIDTH, Math.round(w)));
  }

  function setDisplayedWidth(w) {
    var width = clampWidth(w);
    ui.panel.style.width = width + 'px';
    ui.resizer.setAttribute('aria-valuenow', String(width));
  }

  function saveWidth(w) {
    chosenWidth = clampWidth(w);
    if (!readonly && persistent) {
      try { storage.setItem(widthKey, String(chosenWidth)); } catch (e) { /* 너비 저장 실패는 무시 */ }
    }
    setDisplayedWidth(chosenWidth);
  }

  function loadWidth() {
    if (readonly || !persistent) return;
    var saved = parseInt(storage.getItem(widthKey), 10);
    if (!isNaN(saved)) chosenWidth = saved;
  }

  function setupResizer(handle) {
    handle.addEventListener('pointerdown', function (e) {
      e.preventDefault();
      handle.setPointerCapture(e.pointerId);
      function move(ev) { setDisplayedWidth(window.innerWidth - ev.clientX); }
      function up(ev) {
        handle.releasePointerCapture(e.pointerId);
        handle.removeEventListener('pointermove', move);
        handle.removeEventListener('pointerup', up);
        saveWidth(window.innerWidth - ev.clientX);
      }
      handle.addEventListener('pointermove', move);
      handle.addEventListener('pointerup', up);
    });
    handle.addEventListener('keydown', function (e) {
      var current = ui.panel.getBoundingClientRect().width;
      if (e.key === 'ArrowLeft') { e.preventDefault(); saveWidth(current + 20); }
      else if (e.key === 'ArrowRight') { e.preventDefault(); saveWidth(current - 20); }
    });
  }

  function openPanel(tab) {
    ui.panel.hidden = false;
    ui.toggle.setAttribute('aria-expanded', 'true');
    showTab(tab || ui.tab);
  }

  function closePanel() {
    ui.panel.hidden = true;
    ui.toggle.setAttribute('aria-expanded', 'false');
  }

  function showTab(tab) {
    ui.tab = tab;
    ui.list.hidden = tab !== 'memos';
    ui.summary.hidden = tab !== 'summary';
    Array.prototype.forEach.call(ui.panel.querySelectorAll('.meeting-tab'), function (btn) {
      btn.setAttribute('aria-selected', btn.getAttribute('data-tab') === tab ? 'true' : 'false');
    });
  }

  function buildActions() {
    var items = [h('button', { type: 'button', class: 'meeting-btn', onclick: exportMarkdown }, ['.md로 내보내기'])];
    if (!readonly) {
      items.push(h('button', { type: 'button', class: 'meeting-btn', onclick: exportHtml }, ['메모 포함 HTML로 내보내기']));
      var file = h('input', { type: 'file', accept: '.md,.html,.htm,text/markdown,text/html', hidden: true });
      file.addEventListener('change', function () {
        if (file.files && file.files[0]) importFile(file.files[0]);
        file.value = '';
      });
      items.push(h('button', { type: 'button', class: 'meeting-btn', onclick: function () { file.click(); } }, ['가져오기']), file);
    }
    return h('div', { class: 'meeting-actions' }, items);
  }

  function buildPanel() {
    ui.toggle = h('button', {
      type: 'button', class: 'meeting-toggle', 'data-meeting-ui': '', 'aria-controls': 'meeting-panel', 'aria-expanded': 'false',
      onclick: function () { if (ui.panel.hidden) openPanel(ui.tab); else closePanel(); }
    }, ['메모']);
    ui.resizer = h('div', { class: 'meeting-resizer', role: 'separator', 'aria-orientation': 'vertical', 'aria-label': '패널 너비 조절(←/→)', tabindex: '0' });
    ui.status = h('div', { class: 'meeting-status', role: 'status', 'aria-live': 'polite' });
    ui.notice = h('div', { class: 'meeting-notice-bar', hidden: true });
    ui.list = h('div', { class: 'meeting-list', role: 'tabpanel' });
    ui.summary = h('div', { class: 'meeting-summary', role: 'tabpanel', hidden: true });
    ui.panel = h('aside', { id: 'meeting-panel', class: 'meeting-panel', 'data-meeting-ui': '', 'aria-label': '회의 메모', hidden: true }, [
      ui.resizer,
      h('div', { class: 'meeting-panel-head' }, [
        h('div', { class: 'meeting-tabs', role: 'tablist' }, [
          h('button', { type: 'button', class: 'meeting-tab', role: 'tab', 'data-tab': 'memos', onclick: function () { showTab('memos'); } }, ['메모']),
          h('button', { type: 'button', class: 'meeting-tab', role: 'tab', 'data-tab': 'summary', onclick: function () { showTab('summary'); } }, ['회의 정리'])
        ]),
        h('button', { type: 'button', class: 'meeting-close', 'aria-label': '패널 닫기', onclick: closePanel }, ['×'])
      ]),
      ui.notice,
      ui.status,
      buildActions(),
      ui.list,
      ui.summary
    ]);
    document.body.appendChild(ui.toggle);
    document.body.appendChild(ui.panel);
    setupResizer(ui.resizer);
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && !ui.panel.hidden) closePanel();
    });
    window.addEventListener('resize', function () { setDisplayedWidth(chosenWidth); });
  }

  // ── 메모 카드 ──

  function badge(kind) {
    return h('span', { class: 'meeting-badge meeting-badge--' + kind }, [KIND_LABEL[kind]]);
  }

  function truncate(text, n) {
    return text.length > n ? text.slice(0, n) + '…' : text;
  }

  function locationLabel(m) {
    if (!m.anchor) return h('span', { class: 'meeting-meta' }, ['전체 메모']);
    if (m._loc && m._loc.lost) return h('span', { class: 'meeting-lost' }, ['원래 위치를 찾지 못했습니다']);
    return h('button', { type: 'button', class: 'meeting-quote', onclick: function () { scrollToMemo(m.id); } }, ['“' + truncate(m.anchor.quote, 80) + '”']);
  }

  function conflictText(id) {
    return state.conflicts[id] === 'deleted' ? '다른 탭에서 이 메모를 지웠습니다' : '다른 탭에서 이 메모가 바뀌었습니다';
  }

  function updateCounter(card) {
    var text = card.querySelector('[data-field="text"]');
    var counter = card.querySelector('[data-field="counter"]');
    if (text && counter) counter.textContent = '남은 글자 ' + (core.LIMITS.text - text.value.length);
  }

  function fillCard(card, m) {
    card.className = 'meeting-card meeting-card--' + m.kind;
    card.replaceChildren();
    card.appendChild(h('div', { class: 'meeting-card-head' }, [badge(m.kind), locationLabel(m)]));
    card.appendChild(h('div', { class: 'meeting-conflict', 'data-field': 'conflict', hidden: !state.conflicts[m.id] }, [conflictText(m.id)]));
    if (readonly) {
      card.appendChild(h('p', { class: 'meeting-text' }, [m.text || '(내용 없음)']));
      if (m.kind === 'action') card.appendChild(h('p', { class: 'meeting-meta' }, ['담당자: ' + (m.owner || '-') + ' · 기한: ' + (m.due || '-')]));
      return;
    }
    var kind = h('select', { 'data-field': 'kind', 'aria-label': '메모 종류' }, core.KINDS.map(function (k) {
      return h('option', { value: k, selected: k === m.kind }, [KIND_LABEL[k]]);
    }));
    var text = h('textarea', { 'data-field': 'text', rows: '3', maxlength: String(core.LIMITS.text), 'aria-label': '메모 내용', placeholder: '메모를 입력하세요' });
    text.value = m.text || '';
    var owner = h('input', { type: 'text', 'data-field': 'owner', maxlength: String(core.LIMITS.owner), 'aria-label': '담당자', placeholder: '담당자' });
    owner.value = m.owner || '';
    var due = h('input', { type: 'date', 'data-field': 'due', min: '1000-01-01', max: '9999-12-31', 'aria-label': '기한' });
    due.value = m.due || '';
    [kind, text, owner, due].forEach(function (input) {
      input.addEventListener('input', function () { onEdit(m.id, card); });
    });
    kind.addEventListener('change', function () { onEdit(m.id, card); });
    card.appendChild(kind);
    card.appendChild(text);
    card.appendChild(h('div', { class: 'meeting-counter', 'data-field': 'counter' }));
    card.appendChild(h('div', { class: 'meeting-action-fields', 'data-field': 'action-row', hidden: m.kind !== 'action' }, [owner, due]));
    card.appendChild(h('div', { class: 'meeting-card-foot' }, [
      h('button', { type: 'button', class: 'meeting-delete', onclick: function () { deleteMemo(m.id); } }, ['삭제'])
    ]));
    updateCounter(card);
  }

  function updateCardMeta(card, m) {
    var draft = state.drafts[m.id];
    var kind = (draft && draft.kind) || m.kind;
    card.className = 'meeting-card meeting-card--' + kind;
    card.querySelector('.meeting-card-head').replaceChildren(badge(kind), locationLabel(m));
    var conflict = card.querySelector('[data-field="conflict"]');
    conflict.textContent = conflictText(m.id);
    conflict.hidden = !state.conflicts[m.id];
  }

  function buildCard(m) {
    var card = h('article', { class: 'meeting-card', 'data-memo-id': m.id });
    card.addEventListener('focusout', function (e) {
      if (card.contains(e.relatedTarget)) return;
      // 포커스가 다른 카드로 옮겨간 뒤에 처리해야 그 카드가 편집 중으로 보호된다
      setTimeout(function () {
        if (state.timers[m.id]) commit(m.id);
        resolveConflictOnBlur(m.id);
      }, 0);
    });
    fillCard(card, m);
    return card;
  }

  function sortedForList(keepIds) {
    return allMemos().filter(function (m) {
      return !m.deleted_at || (keepIds[m.id] && state.conflicts[m.id] === 'deleted');
    }).sort(function (a, b) {
      var pa = a._loc && !a._loc.lost ? a._loc.start : Infinity;
      var pb = b._loc && !b._loc.lost ? b._loc.start : Infinity;
      if (pa !== pb) return pa < pb ? -1 : 1;
      return core.compareTime(a.created_at, b.created_at);
    });
  }

  function renderList(opts) {
    // 편집 중인 카드는 다시 채우지도, 옮기지도 않는다(입력 내용과 포커스가 사라진다)
    var keepIds = {};
    Object.keys(ui.cards).forEach(function (id) {
      if (isEditing(id)) keepIds[id] = true;
    });
    if (opts && opts.keepCard) keepIds[opts.keepCard] = true;
    var memos = sortedForList(keepIds);
    var present = {};
    var nodes = memos.map(function (m) {
      present[m.id] = true;
      var card = ui.cards[m.id];
      if (card && keepIds[m.id]) {
        updateCardMeta(card, m);
        return card;
      }
      if (card) fillCard(card, m);
      else card = ui.cards[m.id] = buildCard(m);
      return card;
    });
    Object.keys(ui.cards).forEach(function (id) {
      if (present[id]) return;
      var stale = ui.cards[id];
      if (stale.parentNode) stale.parentNode.removeChild(stale);
      delete ui.cards[id];
    });
    var empty = ui.list.querySelector('.meeting-empty');
    if (empty) empty.parentNode.removeChild(empty);
    var editing = Object.keys(keepIds).some(function (id) { return present[id]; });
    nodes.forEach(function (node) {
      // 편집 중이면 순서를 바꾸지 않고 새 카드만 끝에 붙인다. 아니면 문서 순서대로 다시 배치한다.
      if (!editing || node.parentNode !== ui.list) ui.list.appendChild(node);
    });
    if (!nodes.length) ui.list.appendChild(h('p', { class: 'meeting-empty' }, ['메모가 없습니다. 본문의 「+ 메모」나 드래그 선택으로 추가하세요.']));
  }

  function jumpTo(m) {
    if (m.anchor && m._loc && !m._loc.lost) {
      scrollToMemo(m.id);
    } else {
      showTab('memos');
      focusCard(m.id);
    }
  }

  function summaryItem(m) {
    return h('li', {}, [h('button', { type: 'button', class: 'meeting-link', onclick: function () { jumpTo(m); } }, [m.text || '(내용 없음)'])]);
  }

  function summarySection(title, content) {
    return h('section', {}, [h('h3', {}, [title]), content]);
  }

  function emptyText() {
    return h('p', { class: 'meeting-empty' }, ['없음']);
  }

  function actionTable(actions) {
    return h('table', {}, [
      h('thead', {}, [h('tr', {}, [h('th', {}, ['할 일']), h('th', {}, ['담당자']), h('th', {}, ['기한'])])]),
      h('tbody', {}, actions.map(function (m) {
        return h('tr', {}, [
          h('td', {}, [h('button', { type: 'button', class: 'meeting-link', onclick: function () { jumpTo(m); } }, [m.text || '(내용 없음)'])]),
          h('td', {}, [m.owner || '-']),
          h('td', {}, [m.due || '-'])
        ]);
      }))
    ]);
  }

  function renderSummary() {
    var g = core.summarize(allMemos(), positions());
    ui.summary.replaceChildren(
      summarySection('결정사항', g.decisions.length ? h('ol', {}, g.decisions.map(summaryItem)) : emptyText()),
      summarySection('액션 아이템', g.actions.length ? actionTable(g.actions) : emptyText()),
      summarySection('메모', g.notes.length ? h('ul', {}, g.notes.map(summaryItem)) : emptyText()),
      summarySection('전체 메모', g.general.length ? h('ul', {}, g.general.map(summaryItem)) : emptyText())
    );
  }

  function refresh(opts) {
    relocate();
    renderList(opts);
    renderSummary();
    ui.toggle.textContent = '메모 ' + liveMemos().length;
  }

  function focusCard(id) {
    var card = ui.cards[id];
    if (!card) return;
    card.scrollIntoView({ block: 'nearest' });
    var input = card.querySelector('textarea');
    if (input) input.focus();
    card.classList.add('meeting-card--flash');
    setTimeout(function () { card.classList.remove('meeting-card--flash'); }, 1200);
  }

  function scrollToMemo(id) {
    var safeId = window.CSS && CSS.escape ? CSS.escape(id) : id;
    var marks = root.querySelectorAll('mark.meeting-hl[data-memo-id="' + safeId + '"]');
    if (!marks.length) return;
    marks[0].scrollIntoView({ behavior: 'smooth', block: 'center' });
    Array.prototype.forEach.call(marks, function (mark) { mark.classList.add('meeting-hl--flash'); });
    setTimeout(function () {
      Array.prototype.forEach.call(marks, function (mark) { mark.classList.remove('meeting-hl--flash'); });
    }, 1500);
  }

  // ── 내보내기·가져오기 ──

  function pad(n) {
    return (n < 10 ? '0' : '') + n;
  }

  function fileBase() {
    var d = new Date();
    var title = String(cfg.meeting.title || '회의').replace(/[\\/:*?"<>|\s]+/g, '_').slice(0, 80);
    return title + '_회의메모_' + d.getFullYear() + pad(d.getMonth() + 1) + pad(d.getDate());
  }

  function download(name, type, text) {
    var url = URL.createObjectURL(new Blob([text], { type: type }));
    var a = h('a', { href: url, download: name });
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }

  function currentStore() {
    return { doc: docInfo(), memos: allMemos() };
  }

  function exportMarkdown() {
    var md = core.toMarkdown(currentStore(), { starts_at: cfg.meeting.starts_at, positions: positions() });
    download(fileBase() + '.md', 'text/markdown;charset=utf-8', md);
    setStatus('.md 파일을 저장했습니다');
  }

  function exportHtml() {
    if (!cfg.export_url) return;
    setStatus('메모 포함 HTML을 만드는 중입니다…');
    var theme = document.documentElement.getAttribute('data-theme') || 'github-light';
    var url = cfg.export_url + (cfg.export_url.indexOf('?') === -1 ? '?' : '&') + 'theme=' + encodeURIComponent(theme);
    fetch(url, { credentials: 'same-origin' }).then(function (res) {
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return res.text();
    }).then(function (html) {
      var doc = new DOMParser().parseFromString(html, 'text/html');
      var marks = Array.prototype.filter.call(doc.body.childNodes, function (n) {
        return n.nodeType === Node.COMMENT_NODE && n.data.trim() === EMBED_MARK;
      });
      if (marks.length !== 1) throw new Error('메모를 넣을 자리를 찾지 못했습니다');
      var payload = core.exportPayload(currentStore(), core.nowIso());
      payload.readonly = true;
      var script = doc.createElement('script');
      script.id = core.EMBED_SCRIPT_ID;
      script.textContent = 'window.MEETING_EMBED = ' + core.encodeForScript(payload) + ';';
      marks[0].parentNode.replaceChild(script, marks[0]);
      download(fileBase() + '.html', 'text/html;charset=utf-8', '<!DOCTYPE html>\n' + doc.documentElement.outerHTML);
      setStatus('메모 포함 HTML을 저장했습니다');
    }).catch(function (err) {
      setStatus('메모 포함 HTML을 만들지 못했습니다: ' + err.message + ' (.md 내보내기는 사용할 수 있습니다)', true);
    });
  }

  function importFile(file) {
    file.text().then(function (text) {
      var parsed = core.parseImport(text);
      if (parsed.error) {
        setStatus('가져오지 못했습니다: ' + parsed.error, true);
        return;
      }
      if (parsed.doc.repo_id !== cfg.repo_id || parsed.doc.meeting_id !== cfg.meeting.id) {
        var other = parsed.doc.title || parsed.doc.meeting_id;
        if (!window.confirm('다른 회의(' + other + ')의 메모입니다. 그래도 가져올까요?')) return;
      }
      var current = core.listMemos(storage, memoPrefix);
      var dry = core.merge(current, parsed.memos);
      var restore = dry.deletedConflicts.length > 0 &&
        window.confirm('이 브라우저에서 삭제한 메모 ' + dry.deletedConflicts.length + '건이 파일에 있습니다. 복원할까요?');
      var res = core.merge(current, parsed.memos, { restoreDeleted: restore });
      [].concat(res.added, res.updated, res.restored).forEach(persist);
      refresh();
      setStatus('추가 ' + res.added.length + ', 갱신 ' + res.updated.length + ', 복원 ' + res.restored.length +
        ', 건너뜀 ' + res.skipped.length + ', 형식 오류로 제외 ' + parsed.invalid.length);
    }).catch(function (err) {
      setStatus('파일을 읽지 못했습니다: ' + err.message, true);
    });
  }

  // ── 시작 ──

  function init() {
    cfg = readConfig();
    if (!cfg || !core) return;
    root = document.querySelector(cfg.root_selector);
    if (!root) return;
    embed = window.MEETING_EMBED || null;
    readonly = !!(cfg.readonly || embed);
    memoPrefix = cfg.storage_key + ':memo:';
    docKey = cfg.storage_key + ':doc';
    widthKey = cfg.storage_key + '-panel-width';

    if (readonly) {
      storage = memoryStorage();
      persistent = false;
      ((embed && embed.memos) || []).forEach(function (m) { state.memos[m.id] = core.stripRuntime(m); });
    } else {
      var opened = openStorage();
      storage = opened.storage;
      persistent = opened.persistent;
      try { storage.setItem(docKey, JSON.stringify({ schema: core.SCHEMA, doc: docInfo() })); } catch (e) { /* 저장 실패는 아래 안내로 대신 */ }
      core.purgeTombstones(storage, memoPrefix, Date.now());
      core.listMemos(storage, memoPrefix).forEach(function (m) {
        if (core.validateMemo(m) === null) state.memos[m.id] = m;
      });
      addBlockButtons();
      setupSelection();
      window.addEventListener('storage', onStorage);
    }

    buildPanel();
    loadWidth();
    setDisplayedWidth(chosenWidth);
    if (readonly) showNotice('읽기 전용 사본입니다. 내 메모로 합치려면 Viewer 회의 모드의 「가져오기」에서 이 파일을 선택하세요');
    else if (!persistent) showNotice('이 브라우저에서는 메모가 저장되지 않습니다');
    refresh();
    if (readonly && window.innerWidth >= 1500) openPanel('memos');
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();

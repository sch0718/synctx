/* synctx 회의 근거: 근거 링크 말풍선과 근거 모음 이동. evidence-core.js 다음에 실행된다. */
(function () {
  'use strict';

  var core = (typeof globalThis !== 'undefined' ? globalThis : window).SynctxEvidenceCore;
  var LINK = 'a[data-evidence]';
  var CLOSE_DELAY_MS = 200;
  var HINT = '누르면 근거 모음으로 이동합니다';

  var entries = Object.create(null);
  var pop, popDoc, popLoc, popBody, popHint;
  var current = null;
  // 발표 화면에서 눌러 연 말풍선은 포인터·포커스가 떠나도 다시 누르거나 Esc를 누를 때까지 남는다
  var pinned = false;
  var closeTimer = null;

  function readEntries() {
    var el = document.querySelector('script[type="application/json"][data-synctx-evidence]');
    if (!el) return null;
    try {
      var data = JSON.parse(el.textContent);
      return data && data.schema === 1 && Array.isArray(data.entries) ? data.entries : null;
    } catch (e) {
      return null;
    }
  }

  function freeId(base) {
    var id = base;
    for (var n = 2; document.getElementById(id); n++) id = base + '-' + n;
    return id;
  }

  function part(tag, cls) {
    var node = document.createElement(tag);
    node.className = cls;
    pop.appendChild(node);
    return node;
  }

  function buildPopup() {
    pop = document.createElement('div');
    pop.className = 'evidence-pop';
    pop.id = freeId('evidence-pop');
    pop.setAttribute('role', 'tooltip');
    pop.setAttribute('data-evidence-pop', '');
    pop.setAttribute('data-meeting-ui', '');
    pop.hidden = true;
    popDoc = part('p', 'evidence-pop-doc');
    popLoc = part('p', 'evidence-pop-loc');
    popBody = part('div', 'evidence-pop-body');
    popHint = part('p', 'evidence-pop-hint');
    popHint.textContent = HINT;
    document.body.appendChild(pop);
  }

  function linkOf(node) {
    var link = node && node.closest ? node.closest(LINK) : null;
    return link && entries[link.getAttribute('data-evidence')] ? link : null;
  }

  function shown(el) {
    return !!el && el.getClientRects().length > 0;
  }

  function noHover() {
    return !!window.matchMedia && window.matchMedia('(hover: none)').matches;
  }

  function place(link) {
    pop.style.left = '0px';
    pop.style.top = '0px';
    var r = link.getBoundingClientRect();
    var view = document.documentElement;
    var pos = core.popupPosition(
      { left: r.left, top: r.top, bottom: r.bottom, width: r.width },
      { width: pop.offsetWidth, height: pop.offsetHeight },
      { width: view.clientWidth, height: view.clientHeight });
    pop.style.left = (window.scrollX + pos.left) + 'px';
    pop.style.top = (window.scrollY + pos.top) + 'px';
  }

  function open(link, pin) {
    clearTimeout(closeTimer);
    if (pin) pinned = true;
    if (link === current && !pop.hidden) return;
    if (current) current.removeAttribute('aria-describedby');
    var entry = entries[link.getAttribute('data-evidence')];
    current = link;
    pinned = !!pin;
    popDoc.textContent = String(entry.doc || '');
    popLoc.textContent = String(entry.loc || '');
    popBody.innerHTML = String(entry.html || '');
    popHint.hidden = !shown(document.getElementById(entry.id));
    pop.scrollTop = 0;
    link.setAttribute('aria-describedby', pop.id);
    pop.hidden = false;
    place(link);
  }

  function close() {
    clearTimeout(closeTimer);
    pinned = false;
    if (current) current.removeAttribute('aria-describedby');
    current = null;
    pop.hidden = true;
  }

  function closeLater() {
    if (pinned) return;
    clearTimeout(closeTimer);
    closeTimer = setTimeout(close, CLOSE_DELAY_MS);
  }

  function within(node) {
    return !!node && (pop.contains(node) || (!!current && current.contains(node)));
  }

  function onClick(e) {
    var link = linkOf(e.target);
    if (!link) {
      if (pinned && !pop.contains(e.target)) close();
      return;
    }
    if (core.opensInNewContext(e, link.getAttribute('target'))) return;
    // 발표 화면은 장 구조를 지키려고 근거 모음으로 가지 않고 말풍선만 연다(#slide-N 그대로)
    if (document.body.classList.contains('page-present')) {
      e.preventDefault();
      e.stopPropagation();
      if (link === current && pinned) close();
      else open(link, true);
      return;
    }
    var item = document.getElementById(link.getAttribute('data-evidence'));
    if (!shown(item)) return;
    // 처리한 클릭은 메모 형광펜(<mark>) 클릭 처리로 넘기지 않는다(옮긴 포커스를 메모 카드가 가져가지 않게)
    e.preventDefault();
    e.stopPropagation();
    close();
    // 주소의 해시를 바꾸면 발표 화면의 #slide-N·hashchange 처리와 부딪히므로 스크롤과 포커스만 옮긴다
    item.scrollIntoView({ block: 'start' });
    item.focus({ preventScroll: true });
  }

  function init() {
    var list = readEntries();
    if (!list || !core) return;
    list.forEach(function (entry) {
      if (entry && typeof entry.id === 'string') entries[entry.id] = entry;
    });
    buildPopup();

    document.addEventListener('mouseover', function (e) {
      if (pop.contains(e.target)) {
        clearTimeout(closeTimer);
        return;
      }
      var link = linkOf(e.target);
      if (link && !noHover()) open(link, false);
    });
    document.addEventListener('mouseout', function (e) {
      if (!pop.hidden && within(e.target) && !within(e.relatedTarget)) closeLater();
    });
    // 터치에서는 누르기가 포커스도 옮기므로 포커스 말풍선도 열지 않는다
    document.addEventListener('focusin', function (e) {
      var link = linkOf(e.target);
      if (link && !noHover()) open(link, false);
    });
    document.addEventListener('focusout', function (e) {
      if (current && current.contains(e.target)) closeLater();
    });
    // 캡처 단계라 링크 안 메모 형광펜(누르면 전파를 멈춘다)을 눌러도 근거 링크로 처리한다
    document.addEventListener('click', onClick, true);
    // 캡처 단계에서 받아, 말풍선이 열려 있을 때의 Esc가 메모 패널 닫기·발표 끝내기로 이어지지 않게 한다.
    // 발표 화면에서 다른 키로 장이 바뀌어 링크가 숨으면 말풍선도 닫는다
    window.addEventListener('keydown', function (e) {
      if (pop.hidden) return;
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        close();
      } else if (!shown(current)) {
        close();
      }
    }, true);
    window.addEventListener('resize', function () {
      if (pop.hidden) return;
      if (shown(current)) place(current);
      else close();
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();

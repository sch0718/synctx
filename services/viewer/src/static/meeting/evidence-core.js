/* synctx 회의 근거: DOM이 필요 없는 말풍선 로직. 브라우저와 node --test가 같은 파일을 쓴다. */
(function (root) {
  'use strict';

  var GAP = 6;
  var EDGE = 8;

  // 링크 아래 6px에 두고 화면 아래를 넘으면 링크 위로 뒤집는다. 가로는 화면 양옆 8px 안으로 당긴다
  function popupPosition(link, popup, viewport) {
    var top = link.bottom + GAP;
    var flipped = top + popup.height > viewport.height;
    if (flipped) top = link.top - GAP - popup.height;
    var left = Math.max(EDGE, Math.min(link.left, viewport.width - popup.width - EDGE));
    return { left: left, top: top, flipped: flipped };
  }

  // 가운데 버튼·수정 키·다른 창 target이면 브라우저 기본 동작(원래 href)에 맡긴다
  function opensInNewContext(event, linkTarget) {
    if (event.button === 1 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return true;
    return !!linkTarget && linkTarget !== '_self';
  }

  root.SynctxEvidenceCore = {
    popupPosition: popupPosition,
    opensInNewContext: opensInNewContext
  };
})(typeof globalThis !== 'undefined' ? globalThis : window);

(function (root) {
  'use strict';

  function joinReason(cfg, now) {
    if (cfg.reason) return cfg.reason;
    if (!cfg.enabled) return '영상 회의가 꺼져 있습니다';
    if (cfg.cancelled) return '취소된 회의입니다';
    var start = Date.parse(cfg.joinable_from), end = Date.parse(cfg.joinable_until);
    if (!Number.isFinite(start) || !Number.isFinite(end)) return '참여 시간을 확인할 수 없습니다';
    if (now < start) return '회의 시작 15분 전부터 참여할 수 있습니다';
    if (now >= end) return '회의 종료 후 1시간이 지났습니다';
    return '';
  }

  function resolveAnchor(anchor, ids) {
    if (typeof anchor !== 'string' || !anchor || anchor.length > 256 || /[\x00-\x1f\x7f-\x9f]/.test(anchor)) return null;
    if (ids.indexOf(anchor) !== -1) return anchor;
    try {
      var decoded = decodeURIComponent(anchor.replace(/^#/, ''));
      if (!decoded || decoded.length > 256 || /[\x00-\x1f\x7f-\x9f]/.test(decoded)) return null;
      return ids.indexOf(decoded) !== -1 ? decoded : null;
    } catch (e) { return null; }
  }

  function validateMessage(text, doc, moderator, lastTs) {
    if (moderator !== true || typeof text !== 'string' || text.length > 2048 || new TextEncoder().encode(text).length > 2048) return null;
    var msg;
    try { msg = JSON.parse(text); } catch (e) { return null; }
    if (!msg || Array.isArray(msg) || typeof msg !== 'object' || msg.schema !== 'synctx-follow/1' || msg.doc !== doc) return null;
    if (Object.keys(msg).some(function (key) { return ['schema', 'doc', 'anchor', 'slide', 'ts'].indexOf(key) === -1; })) return null;
    if (!Number.isSafeInteger(msg.ts) || msg.ts <= 0 || msg.ts <= (lastTs || 0)) return null;
    var hasAnchor = Object.prototype.hasOwnProperty.call(msg, 'anchor');
    var hasSlide = Object.prototype.hasOwnProperty.call(msg, 'slide');
    if (hasAnchor === hasSlide) return null;
    if (hasSlide && (!Number.isInteger(msg.slide) || msg.slide < 1 || msg.slide > 10000)) return null;
    if (hasAnchor && (typeof msg.anchor !== 'string' || !msg.anchor || msg.anchor.length > 256 || /[\x00-\x1f\x7f-\x9f]/.test(msg.anchor))) return null;
    return msg;
  }

  function shouldSend(last, now, interval) {
    return Number.isFinite(now) && (last === null || now - last >= interval);
  }

  function moderatorFor(id, info) {
    if (typeof id !== 'string' || !id) return false;
    return !!info && Array.isArray(info.rooms) && info.rooms.some(function (room) {
      return room && Array.isArray(room.participants) && room.participants.some(function (p) {
        return p && p.id === id && p.role === 'moderator';
      });
    });
  }

  var api = { joinReason: joinReason, resolveAnchor: resolveAnchor, validateMessage: validateMessage,
    shouldSend: shouldSend, moderatorFor: moderatorFor };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.SynctxJitsiCore = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);

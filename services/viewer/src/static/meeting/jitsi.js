(function () {
  'use strict';
  var data = document.getElementById('jitsi-config');
  if (!data) return;
  var cfg;
  try { cfg = JSON.parse(data.textContent); } catch (e) { return; }
  var core = window.SynctxJitsiCore;
  var article = document.querySelector('article.markdown-body');
  var buttons = Array.from(document.querySelectorAll('[data-jitsi-join]'));
  var api = null, panel = null, originButton = null, busy = false, scriptPromise = null;
  var moderator = false, following = true, followButton, status, host;
  var roomsCache = null, received = Object.create(null);
  var lastSent = null, sendTimer = null, suppressUntil = 0, currentSlide = null;
  var presenting = document.body.classList.contains('page-present');
  var attemptId = 0;

  function updateButtons() {
    var reason = core.joinReason(cfg, Date.now());
    buttons.forEach(function (button) {
      button.disabled = busy || !!reason;
      button.setAttribute('aria-expanded', panel && !panel.hidden ? 'true' : 'false');
    });
    document.querySelectorAll('[data-jitsi-reason]').forEach(function (el) {
      if (el.textContent !== reason) el.textContent = reason;
    });
  }

  function setFollowing(value) {
    following = value;
    followButton.setAttribute('aria-pressed', String(value));
    followButton.textContent = value ? '따라가기: 켬' : '따라가기: 끔';
  }

  function close() {
    attemptId += 1;
    if (sendTimer !== null) window.clearTimeout(sendTimer);
    sendTimer = null;
    if (api) api.dispose();
    api = null;
    roomsCache = null;
    received = Object.create(null);
    lastSent = null;
    if (panel) panel.hidden = true;
    updateButtons();
    if (originButton) originButton.focus();
  }

  function makePanel() {
    if (panel) return;
    panel = document.createElement('section');
    panel.id = 'jitsi-panel';
    panel.className = 'jitsi-panel';
    panel.setAttribute('role', 'region');
    panel.setAttribute('aria-label', '영상 회의');
    panel.hidden = true;
    panel.addEventListener('keydown', function (event) {
      if (event.key === 'Escape' && !panel.hidden && panel.contains(document.activeElement)) {
        event.preventDefault();
        panel.hidden = true;
        updateButtons();
        if (originButton) originButton.focus();
      }
    });
    var title = document.createElement('h2');
    title.textContent = '영상 회의';
    title.tabIndex = -1;
    var closeButton = document.createElement('button');
    closeButton.type = 'button';
    closeButton.className = 'btn';
    closeButton.textContent = '영상 회의 닫기';
    closeButton.addEventListener('click', close);
    followButton = document.createElement('button');
    followButton.type = 'button';
    followButton.className = 'btn';
    followButton.setAttribute('aria-label', '발표자 따라가기');
    followButton.addEventListener('click', function () { setFollowing(!following); });
    status = document.createElement('p');
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    host = document.createElement('div');
    host.className = 'jitsi-frame';
    panel.append(title, closeButton, followButton, status, host);
    document.body.appendChild(panel);
  }

  function loadScript() {
    if (scriptPromise) return scriptPromise;
    scriptPromise = new Promise(function (resolve, reject) {
      var script = document.createElement('script');
      var timeout = window.setTimeout(failed, 15000);
      function failed() {
        window.clearTimeout(timeout);
        script.remove();
        scriptPromise = null;
        reject(new Error('script'));
      }
      script.src = cfg.public_url + '/external_api.js';
      script.onload = function () {
        window.clearTimeout(timeout);
        if (typeof window.JitsiMeetExternalAPI === 'function') resolve();
        else failed();
      };
      script.onerror = failed;
      document.head.appendChild(script);
    });
    return scriptPromise;
  }

  async function receive(event) {
    if (!api || moderator || !following) return;
    var id = event.senderInfo && event.senderInfo.id;
    if (typeof id !== 'string' || !id) return;
    var activeApi = api, receivedAt = Date.now();
    if (!roomsCache || receivedAt - roomsCache.at >= 1000) {
      roomsCache = { at: receivedAt, promise: Promise.resolve().then(function () {
        return activeApi.getRoomsInfo();
      }).catch(function () { return null; }) };
    }
    var snapshot = roomsCache;
    var info = await snapshot.promise;
    if (api !== activeApi || roomsCache !== snapshot || moderator || !following || Date.now() - receivedAt > 15000) return;
    var msg = core.validateMessage(event.eventData && event.eventData.text, cfg.doc,
      core.moderatorFor(id, info), received[id]);
    if (!msg) return;
    received[id] = msg.ts;
    if (msg.slide !== undefined) {
      if (!presenting) return;
      suppressUntil = Date.now() + 700;
      document.dispatchEvent(new CustomEvent('synctx-follow-slide', { detail: { slide: msg.slide } }));
    } else {
      var ids = Array.from(article.querySelectorAll('[id]')).map(function (el) { return el.id; });
      var anchor = core.resolveAnchor(msg.anchor, ids);
      var target = anchor && document.getElementById(anchor);
      if (!target || !article.contains(target)) return;
      suppressUntil = Date.now() + 700;
      if (presenting) {
        window.location.hash = encodeURIComponent(anchor);
      } else target.scrollIntoView({ block: 'start', behavior: 'instant' });
    }
  }

  function sendPosition() {
    sendTimer = null;
    if (!api || !moderator) return;
    var now = Date.now();
    if (!core.shouldSend(lastSent, now, 250)) {
      sendTimer = window.setTimeout(sendPosition, 250 - (now - lastSent));
      return;
    }
    var msg = { schema: 'synctx-follow/1', doc: cfg.doc, ts: now };
    if (presenting) {
      if (!currentSlide) return;
      msg.slide = currentSlide;
    } else {
      var headings = Array.from(article.querySelectorAll('h1[id], h2[id], h3[id], h4[id], h5[id], h6[id]'));
      var target = headings[0];
      headings.forEach(function (el) { if (el.getBoundingClientRect().top <= 130) target = el; });
      if (!target || !core.resolveAnchor(target.id, [target.id])) return;
      msg.anchor = target.id;
    }
    lastSent = now;
    api.executeCommand('sendEndpointTextMessage', '', JSON.stringify(msg));
  }

  function scheduleSend() {
    if (!api || !moderator || sendTimer !== null) return;
    sendTimer = window.setTimeout(sendPosition, 0);
  }

  async function join(event) {
    if (busy || core.joinReason(cfg, Date.now())) return;
    originButton = event.currentTarget;
    if (api) { panel.hidden = false; panel.querySelector('h2').focus(); updateButtons(); return; }
    busy = true;
    var attempt = ++attemptId;
    updateButtons();
    makePanel();
    panel.hidden = false;
    status.textContent = '영상 회의에 연결하고 있습니다';
    panel.querySelector('h2').focus();
    try {
      var response = await fetch(cfg.endpoint, { credentials: 'same-origin', cache: 'no-store' });
      var token = await response.json();
      if (attempt !== attemptId) return;
      if (!response.ok) {
        status.textContent = token.detail && token.detail.message || '영상 회의 참여 권한을 확인하세요';
        return;
      }
      if (token.public_url !== cfg.public_url) throw new Error('origin');
      await loadScript();
      if (attempt !== attemptId) return;
      moderator = token.moderator === true;
      followButton.hidden = moderator;
      setFollowing(true);
      api = new window.JitsiMeetExternalAPI(new URL(token.public_url).host, {
        roomName: token.room, jwt: token.jwt, parentNode: host, width: '100%', height: '100%'
      });
      var iframe = api.getIFrame();
      iframe.title = 'Jitsi 영상 회의';
      api.addListener('endpointTextMessageReceived', receive);
      api.addListener('participantJoined', function () { roomsCache = null; scheduleSend(); });
      api.addListener('participantLeft', function (event) { roomsCache = null; delete received[event.id]; });
      api.addListener('readyToClose', close);
      api.addListener('videoConferenceJoined', scheduleSend);
      api.addListener('dataChannelOpened', scheduleSend);
      status.textContent = moderator ? '발표자 화면 위치를 공유합니다' : '발표자를 따라갑니다. 직접 스크롤하면 따라가기가 꺼집니다';
    } catch (e) {
      if (api) { api.dispose(); api = null; }
      status.textContent = '영상 회의에 연결하지 못했습니다. 다시 참여해 주세요';
    } finally {
      busy = false;
      updateButtons();
    }
  }

  buttons.forEach(function (button) { button.addEventListener('click', join); });
  makePanel();
  document.addEventListener('synctx-slide-changed', function (event) { currentSlide = event.detail.slide; scheduleSend(); });
  if (presenting) {
    var progress = document.querySelector('.present-progress');
    currentSlide = progress && Number(progress.getAttribute('aria-valuenow'));
  }
  window.addEventListener('scroll', function () {
    if (api && !moderator && following && Date.now() >= suppressUntil) {
      setFollowing(false);
      status.textContent = '직접 스크롤하여 따라가기가 꺼졌습니다';
    }
    scheduleSend();
  }, { passive: true });
  updateButtons();
  window.setInterval(updateButtons, 1000);
  window.addEventListener('pagehide', function () { if (api) api.dispose(); });
})();

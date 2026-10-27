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

  var FINDER_FILE_LIMIT = 8;
  var FINDER_CONTENT_LIMIT = 5;
  var SEARCH_MIN = 2;
  var SEARCH_MAX = 100;

  function isSearchable(query) {
    var length = Array.from(String(query || '').trim()).length;
    return length >= SEARCH_MIN && length <= SEARCH_MAX;
  }

  function finderOptions(fileMatches, searchData, query) {
    var options = (fileMatches || []).slice(0, FINDER_FILE_LIMIT).map(function (path) {
      return { kind: 'file', group: '파일', path: path };
    });
    var repos = searchData && Array.isArray(searchData.repos) ? searchData.repos : [];
    var content = [];
    for (var i = 0; i < repos.length && content.length < FINDER_CONTENT_LIMIT; i++) {
      var repo = repos[i];
      if (!repo || repo.status !== 'ok' || !Array.isArray(repo.matches)) continue;
      for (var j = 0; j < repo.matches.length && content.length < FINDER_CONTENT_LIMIT; j++) {
        var m = repo.matches[j];
        content.push({
          kind: 'content', group: '본문', repo: repo.id, repoName: repo.name || repo.id, path: m.path, line: m.line,
          before: m.before, match: m.match, after: m.after,
        });
      }
    }
    options = options.concat(content);
    if (isSearchable(query)) options.push({ kind: 'all', group: null });
    return options;
  }

  function encodePath(path) { return String(path).split('/').map(encodeURIComponent).join('/'); }

  // ctx: { viewPrefix, activeRepo, activeRef, query }. ref는 지금 보고 있는 저장소로 갈 때만 붙인다
  function finderHref(option, ctx) {
    var ref = ctx.activeRef ? 'ref=' + encodeURIComponent(ctx.activeRef) : '';
    if (option.kind === 'all') {
      var href = ctx.viewPrefix + '/search?q=' + encodeURIComponent(ctx.query);
      if (ctx.activeRepo) {
        href += '&repo=' + encodeURIComponent(ctx.activeRepo);
        if (ref) href += '&' + ref + '&ref_repo=' + encodeURIComponent(ctx.activeRepo);
      }
      return href;
    }
    var repo = option.kind === 'file' ? ctx.activeRepo : option.repo;
    return ctx.viewPrefix + '/' + encodeURIComponent(repo) + '/' + encodePath(option.path) +
      (repo === ctx.activeRepo && ref ? '?' + ref : '');
  }

  // 저장소를 보고 있는데 파일 목록이 아직 없으면(받는 중·실패) 그 상태를 알린다
  function finderFileStatus(activeRepo, files, loadFailed) {
    if (!activeRepo || files !== null) return null;
    return loadFailed ? '파일 목록을 불러오지 못했습니다. 검색어를 고치면 다시 시도합니다.' : '파일 목록을 불러오는 중…';
  }

  // 요청 번호가 지금 번호와 같은 응답만 넘긴다. 같은 검색어로 돌아와도 앞 요청의 응답은 버린다
  function createSearchScheduler(opts) {
    var setTimer = opts.setTimer || function (fn, ms) { return setTimeout(fn, ms); };
    var clearTimer = opts.clearTimer || function (t) { clearTimeout(t); };
    var timer = null;
    var controller = null;
    var seq = 0;

    function stop() {
      seq += 1;
      if (timer !== null) { clearTimer(timer); timer = null; }
      if (controller) { controller.abort(); controller = null; }
    }

    return {
      schedule: function (query) {
        stop();
        var mine = seq;
        timer = setTimer(function () {
          timer = null;
          controller = new AbortController();
          var result;
          try {
            result = Promise.resolve(opts.request(query, controller.signal));
          } catch (e) {
            return;
          }
          result.then(function (data) {
            if (mine === seq) opts.onResult(query, data);
          }).catch(function () { return; });
        }, opts.delay);
      },
      cancel: stop,
    };
  }

  var MINUTE_MS = 60000;
  var DAY_MS = 24 * 60 * MINUTE_MS;

  // 분은 내림, 시작까지 남은 분만 올림. 시각이 숫자가 아니면 null
  function meetingStatus(startsAtMs, durationMin, nowMs, cancelled) {
    if (![startsAtMs, durationMin, nowMs].every(function (n) { return typeof n === 'number' && isFinite(n); })) return null;
    if (cancelled) return { kind: 'cancelled', label: '취소된 회의' };
    var endsAtMs = startsAtMs + durationMin * MINUTE_MS;
    if (nowMs < startsAtMs) {
      var until = startsAtMs - nowMs;
      if (until >= DAY_MS) return { kind: 'before', label: '시작 전' };
      return { kind: 'before', label: '시작 전 · ' + Math.max(1, Math.ceil(until / MINUTE_MS)) + '분 후' };
    }
    if (nowMs >= endsAtMs) return { kind: 'after', label: '끝남' };
    var elapsedMin = Math.floor((nowMs - startsAtMs) / MINUTE_MS);
    var remainingMin = Math.floor((endsAtMs - nowMs) / MINUTE_MS);
    return {
      kind: 'live', label: '진행 중 · ' + elapsedMin + '분 지남 · ' + remainingMin + '분 남음',
      elapsedMin: elapsedMin, remainingMin: remainingMin,
    };
  }

  function presentTimeLabel(status) {
    if (!status) return '';
    if (status.kind === 'live') return status.elapsedMin + '분 지남 · ' + status.remainingMin + '분 남음';
    return status.label;
  }

  // 제목 id마다 그 제목이 속한 h2 id. 첫 h2 앞 제목과 h1은 null
  function sectionOwners(headings) {
    var owners = {};
    var owner = null;
    headings.forEach(function (h) {
      if (h.level <= 1) owner = null;
      else if (h.level === 2) owner = h.id;
      owners[h.id] = h.level <= 1 ? null : owner;
    });
    return owners;
  }

  // 본문 최상위 요소마다 장 번호. 첫 요소 뒤에 나오는 H2가 새 장을 연다
  function buildSlides(tags) {
    var slide = 0;
    return tags.map(function (tag, i) {
      if (tag === 'H2' && i > 0) slide += 1;
      return slide;
    });
  }

  function slideFromHash(hash, total) {
    var m = /^#slide-(\d+)$/.exec(hash || '');
    if (!m || total <= 0) return 0;
    return Math.min(Math.max(parseInt(m[1], 10) - 1, 0), total - 1);
  }

  // 입력 중이거나 조합키를 누르면 가로채지 않는다. 버튼·링크 위 Space는 그 요소의 기본 동작에 맡긴다
  function presentKeyAction(key, opts) {
    if (opts.typing || opts.modKey) return null;
    if (key === ' ') {
      if (opts.onControl) return null;
      return opts.shiftKey ? 'prev' : 'next';
    }
    if (key === 'ArrowRight' || key === 'PageDown') return 'next';
    if (key === 'ArrowLeft' || key === 'PageUp') return 'prev';
    if (key === 'Home') return 'first';
    if (key === 'End') return 'last';
    if (key === 'n' || key === 'N') return 'memo';
    if (key === 'Escape') return opts.panelOpen ? null : 'exit';
    return null;
  }

  // 안내는 '다시 보지 않기'를 저장한 브라우저에서만 저절로 뜨지 않는다
  var MEETING_GUIDE_HIDDEN = 'hidden';
  function meetingGuideAutoOpen(stored) { return stored !== MEETING_GUIDE_HIDDEN; }

  function renderViewportHeight(viewportHeight, barsHeight) {
    if (typeof viewportHeight !== 'number' || !isFinite(viewportHeight) || viewportHeight < 0) return 0;
    if (typeof barsHeight !== 'number' || !isFinite(barsHeight) || barsHeight < 0) barsHeight = 0;
    return Math.max(0, viewportHeight - barsHeight);
  }

  function renderAspectRatio(aspect) {
    var fallback = 16 / 9;
    if (typeof aspect === 'number') return isFinite(aspect) && aspect > 0 ? aspect : fallback;
    if (typeof aspect !== 'string') return fallback;
    var parts = /^\s*(\d+(?:\.\d*)?|\.\d+)\s*\/\s*(\d+(?:\.\d*)?|\.\d+)\s*$/.exec(aspect);
    if (!parts) return fallback;
    var width = Number(parts[1]);
    var height = Number(parts[2]);
    var ratio = width / height;
    return isFinite(width) && isFinite(height) && isFinite(ratio) && width > 0 && height > 0 && ratio > 0 ? ratio : fallback;
  }

  root.SynctxViewerCore = {
    RECENT_LIMIT: RECENT_LIMIT,
    matchFiles: matchFiles,
    addRecent: addRecent,
    parseRecent: parseRecent,
    createFileLoader: createFileLoader,
    branchChoices: branchChoices,
    finderOptions: finderOptions,
    finderHref: finderHref,
    finderFileStatus: finderFileStatus,
    isSearchable: isSearchable,
    createSearchScheduler: createSearchScheduler,
    meetingStatus: meetingStatus,
    presentTimeLabel: presentTimeLabel,
    sectionOwners: sectionOwners,
    buildSlides: buildSlides,
    slideFromHash: slideFromHash,
    presentKeyAction: presentKeyAction,
    MEETING_GUIDE_HIDDEN: MEETING_GUIDE_HIDDEN,
    meetingGuideAutoOpen: meetingGuideAutoOpen,
    renderViewportHeight: renderViewportHeight,
    renderAspectRatio: renderAspectRatio,
  };
})(typeof globalThis !== 'undefined' ? globalThis : this);

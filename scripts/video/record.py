#!/usr/bin/env python3
"""실제 Viewer에서 내레이션에 맞춰 여덟 장면을 한 흐름으로 녹화한다."""

from __future__ import annotations

import argparse
import base64
import json
import math
import random
import shutil
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from playwright.sync_api import BrowserContext, Error, Locator, sync_playwright

HERE = Path(__file__).resolve().parent
SIZE = {"width": 1280, "height": 720}
GAP = 0.35
TAIL = 0.7

# 대화 상자 위에도 커서와 클릭 표시를 올린다. 자막은 별도 SRT로 만든다.
OVERLAY = r"""(() => {
  function install() {
    if (document.getElementById('video-overlay')) return;
    const layer = document.createElement('div');
    layer.id = 'video-overlay';
    layer.setAttribute('popover', 'manual');
    layer.setAttribute('aria-hidden', 'true');
    layer.style.cssText = 'position:fixed;inset:0;margin:0;padding:0;border:0;'
      + 'width:100vw;height:100vh;max-width:none;max-height:none;'
      + 'background:transparent;pointer-events:none;overflow:hidden;';
    const shadow = layer.attachShadow({mode:'open'});
    shadow.innerHTML = `<style>
      :host, * {pointer-events:none!important;box-sizing:border-box;}
      :host::backdrop {pointer-events:none;background:transparent;}
      #cursor {position:absolute;left:0;top:0;width:25px;height:32px;
        filter:drop-shadow(1px 2px 2px #333);}
      #ring {position:absolute;left:0;top:0;width:36px;height:36px;
        border:3px solid #ffba37;border-radius:50%;opacity:0;}
    </style><svg id="cursor" viewBox="0 0 30 38">
      <path d="M2 2L2 29L9 23L15 35L21 32L15 21L26 21Z"
      fill="white" stroke="#18212d" stroke-width="2"/></svg><div id="ring"></div>`;
    document.body.appendChild(layer);
    const cursor = shadow.getElementById('cursor');
    const ring = shadow.getElementById('ring');
    const raise = () => {
      try {
        if (layer.matches(':popover-open')) layer.hidePopover();
        layer.showPopover();
      } catch (_) {} // 문서 이동 중이면 다음 조작 때 다시 올린다.
    };
    const move = (x, y) => {
      cursor.style.transform = `translate(${x - 2}px,${y - 2}px)`;
      try { sessionStorage.setItem('video-cursor', JSON.stringify([x,y])); } catch (_) {}
    };
    let xy = [640,360];
    try { xy = JSON.parse(sessionStorage.getItem('video-cursor')) || xy; } catch (_) {}
    move(...xy);
    window.videoOverlay = {raise};
    document.addEventListener('mousemove', e => move(e.clientX, e.clientY), true);
    document.addEventListener('mousedown', e => {
      ring.style.left = `${e.clientX - 18}px`; ring.style.top = `${e.clientY - 18}px`;
      ring.animate([{opacity:1,transform:'scale(.3)'},{opacity:0,transform:'scale(1.8)'}],
        {duration:550});
    }, true);
    document.addEventListener('click', () => queueMicrotask(raise), true);
    raise();
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', install, {once:true});
  } else install();
})()"""


def load_voice(out: Path, scenes: list[dict]) -> dict[tuple[str, int], dict]:
    """대본과 음성의 대응을 녹화 전에 확인한다. 길이를 추정하지 않는다."""
    manifest = out / "voice" / "lines.json"
    if not manifest.is_file():
        raise ValueError("out/voice/lines.json이 없습니다. 먼저 make.sh voice를 실행하세요")
    voice = json.loads(manifest.read_text(encoding="utf-8"))
    expected = [(s["key"], i, text) for s in scenes for i, text in enumerate(s["lines"])]
    if [(v["scene"], v["i"], v["t"]) for v in voice] != expected:
        raise ValueError("음성 목록의 개수·순서·원문이 scenes.json과 다릅니다. 음성을 다시 만드세요")
    result = {}
    for item in voice:
        length = item["dur"]
        if isinstance(length, bool) or not isinstance(length, (int, float)) or not math.isfinite(length) or length <= 0:
            raise ValueError("음성 목록에 올바르지 않은 문장 길이가 있습니다")
        path = Path(item["file"])
        if not path.is_absolute():
            path = manifest.parent / path
        if not path.is_file() or path.suffix.lower() != ".wav":
            raise ValueError(f"문장 음성이 없습니다: {path}")
        result[item["scene"], item["i"]] = item
    return result


class Recorder:
    def __init__(self, context: BrowserContext, base_url: str, out: Path, voice: dict):
        self.context = context
        self.base_url = base_url.rstrip("/")
        self.out = out
        self.voice = voice
        self.rng = random.Random(20261114)
        self.cursor = (640.0, 360.0)
        context.add_init_script(OVERLAY)
        self.page = context.new_page()
        self.page.set_default_timeout(20000)
        self.cdp = None
        self.cast_handler = None
        self.t0: float | None = None
        self.voice_free = -GAP
        self.scene = ""
        self.scene_marks: list[dict] = []
        self.lines: list[dict] = []
        self.frames: list[dict] = []
        self.frame_error: str | None = None
        self.switch_after: float | None = None

    def now(self) -> float:
        if self.frame_error:
            raise RuntimeError(self.frame_error)
        if self.t0 is None:
            raise RuntimeError("녹화 시작 시각이 없습니다")
        return time.time() - self.t0

    def wait(self, seconds: float) -> None:
        # Playwright의 대기를 써야 CDP 프레임·이벤트도 계속 처리된다.
        self.page.wait_for_timeout(max(0, seconds) * 1000)

    def raise_overlay(self) -> None:
        self.page.wait_for_function("Boolean(window.videoOverlay)")
        self.page.evaluate("() => window.videoOverlay.raise()")

    def start_cast(self) -> None:
        session = self.context.new_cdp_session(self.page)
        self.cdp = session

        def frame(event: dict) -> None:
            try:
                if self.t0 is not None:
                    stamp = event["metadata"]["timestamp"] - self.t0
                    if stamp >= 0:
                        if self.switch_after is not None:
                            if stamp <= self.switch_after:
                                # 새 탭이 처음에 보내는 이전 시각의 프레임은 버리고 다음 프레임을 기다린다.
                                return
                            self.switch_after = None
                        if not self.frames or stamp > self.frames[-1]["t"]:
                            file = self.out / "frames" / f"{len(self.frames):06d}.jpg"
                            file.write_bytes(base64.b64decode(event["data"]))
                            self.frames.append({"file": str(file), "t": stamp})
            except (OSError, KeyError, TypeError, ValueError):
                self.frame_error = "스크린캐스트 프레임을 저장하지 못했습니다"
            finally:
                session.send("Page.screencastFrameAck", {"sessionId": event["sessionId"]})

        self.cast_handler = frame
        session.on("Page.screencastFrame", frame)
        session.send("Page.startScreencast", {
            "format": "jpeg", "quality": 88, "maxWidth": 1920, "maxHeight": 1080, "everyNthFrame": 1,
        })
        if self.t0 is None:
            self.t0 = time.time()

    def stop_cast(self) -> None:
        if self.cdp is not None:
            self.cdp.send("Page.stopScreencast")
            self.cdp.remove_listener("Page.screencastFrame", self.cast_handler)
            self.cdp.detach()
            self.cdp = None

    def move(self, x: float, y: float, seconds: float | None = None) -> None:
        x, y = min(1260, max(8, x)), min(700, max(8, y))
        ax, ay = self.cursor
        dx, dy = x - ax, y - ay
        distance = math.hypot(dx, dy)
        if distance < 1:
            return
        seconds = seconds if seconds is not None else min(1.1, max(0.28, 0.26 + distance * 0.00075))
        steps = max(8, round(seconds / 0.016))
        bend = self.rng.uniform(-0.5, 0.5) * min(80, distance * 0.18)
        started = time.monotonic()
        for i in range(1, steps + 1):
            t = i / steps
            ease = 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2
            arc = math.sin(math.pi * t) * bend
            self.page.mouse.move(ax + dx * ease - dy / distance * arc, ay + dy * ease + dx / distance * arc)
            self.wait(started + seconds * t - time.monotonic())
        self.cursor = (x, y)

    def wander_until(self, until: float) -> None:
        center = self.cursor
        while until - self.now() > 0.25:
            remaining = until - self.now()
            self.move(center[0] + self.rng.uniform(-9, 9), center[1] + self.rng.uniform(-6, 6),
                      min(self.rng.uniform(0.3, 0.6), remaining))
            self.wait(min(self.rng.uniform(0.2, 0.45), max(0, until - self.now())))
        self.wait(until - self.now())

    def move_to(self, locator: Locator) -> None:
        locator.wait_for(state="visible")
        locator.scroll_into_view_if_needed()
        box = locator.bounding_box()
        if box is None:
            raise RuntimeError("조작할 요소의 화면 위치를 찾지 못했습니다")
        self.move(box["x"] + box["width"] / 2, box["y"] + min(box["height"] / 2, 45))

    def click(self, locator: Locator) -> None:
        self.move_to(locator)
        self.wait(self.rng.uniform(0.12, 0.22))
        locator.click(delay=self.rng.uniform(70, 110))
        self.wait(self.rng.uniform(0.15, 0.25))
        self.raise_overlay()

    def type_slow(self, text: str) -> None:
        for character in text:
            self.page.keyboard.type(character)
            self.wait(self.rng.uniform(0.08, 0.17))

    def say(self, i: int) -> None:
        self.wander_until(self.voice_free + GAP)
        self.raise_overlay()
        stamp = self.now()
        length = self.voice[self.scene, i]["dur"]
        self.lines.append({"scene": self.scene, "i": i, "t": stamp, "dur": length})
        self.voice_free = stamp + length

    def ready(self) -> None:
        self.page.evaluate("() => document.fonts.ready")
        self.raise_overlay()
        self.scene_marks[-1]["ready"] = self.now()

    def button(self, name: str) -> Locator:
        return self.page.get_by_role("button", name=name, exact=True)

    def link(self, name: str) -> Locator:
        return self.page.get_by_role("link", name=name, exact=True)

    def section(self, name: str) -> None:
        self.page.get_by_role("heading", name=name, exact=True).evaluate(
            "element => element.scrollIntoView({block:'start', behavior:'smooth'})")
        self.wait(0.75)

    def select(self, label: str, value: str) -> None:
        # 1280px는 767px 모바일 구간 밖이므로 상단 컨트롤이 보인다.
        control = self.page.locator(".shell-header").get_by_role("combobox", name=label, exact=True)
        if label == "브랜치":
            # 브랜치 목록은 viewer.js가 비동기로 채운다. 숨겨진 option은 visible을 기다리지 않는다.
            control.locator(f'option[value="{value}"]').wait_for(state="attached")
        self.move_to(control)
        self.wait(self.rng.uniform(0.2, 0.35))
        # 문서의 테마는 쿼리 방식이라 선택만 해도 다시 열린다.
        # select_option만 사용하고 선택 목록은 절대로 펼치지 않는다.
        navigates = label == "브랜치" or self.page.locator("body").get_attribute("data-theme-mode") == "query"
        if navigates:
            with self.page.expect_navigation(wait_until="load"):
                control.select_option(value)
        else:
            control.select_option(value)
        self.raise_overlay()

    def tree_document(self, folder: str, name: str) -> None:
        tree = self.page.locator("#file-tree")
        target = tree.get_by_role("link", name=name, exact=True)
        tree.get_by_role("button", name=folder + "/", exact=True).wait_for(state="visible")
        if not target.is_visible():
            self.click(tree.get_by_role("button", name=folder + "/", exact=True))
        self.click(target)

    def download(self, locator: Locator, filename: str) -> None:
        with self.page.expect_download(timeout=120000) as pending:
            self.click(locator)
        downloaded = pending.value
        if downloaded.failure():
            raise RuntimeError("파일을 내려받지 못했습니다")
        target = self.out / "downloads" / self.scene / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        downloaded.save_as(target)

    def browse(self) -> None:
        card = self.page.get_by_role("link").filter(has=self.page.get_by_text("예시 문서", exact=True))
        card.wait_for(state="visible")
        self.ready()
        self.say(0)
        self.move_to(self.page.get_by_role("heading", level=1))
        self.say(1)
        self.move_to(card)
        self.say(2)
        self.say(3)
        self.click(card)
        self.move_to(self.page.locator("#file-tree"))
        self.say(4)
        self.tree_document("설계", "시스템-구성.md")
        self.page.get_by_role("heading", name="도서 대여 시스템 구성", exact=True).wait_for()
        self.say(5)
        self.section("구성 요소")
        self.wait(0.7)
        self.section("처리 흐름")
        self.page.locator(".mermaid svg").wait_for(state="visible")
        self.wait(0.7)
        self.section("대여 처리 예시")
        self.say(6)
        toc = self.page.get_by_role("complementary", name="이 문서의 목차")
        self.click(toc.get_by_role("link", name="서비스 개요", exact=True))

    def search(self) -> None:
        self.page.keyboard.press("Meta+k")
        search = self.page.get_by_role("combobox", name="문서 검색", exact=True)
        search.wait_for(state="visible")
        self.page.wait_for_function("document.activeElement.id === 'finder-input'")
        self.ready()
        self.say(0)
        self.say(1)
        self.type_slow("대여")
        results = self.page.get_by_role("listbox", name="검색 결과", exact=True)
        files = results.get_by_role("group", name="파일", exact=True)
        body = results.get_by_role("group", name="본문", exact=True)
        files.wait_for(state="visible")
        body.wait_for(state="visible")
        self.say(2)
        self.move_to(files.locator(".finder-group"))
        self.wait(0.6)
        self.move_to(body.locator(".finder-group"))
        self.say(3)
        self.page.keyboard.press("ArrowDown")
        self.wait(0.4)
        self.page.keyboard.press("ArrowDown")
        self.wait(0.4)
        with self.page.expect_navigation(wait_until="load"):
            self.page.keyboard.press("Enter")
        self.say(4)
        self.page.keyboard.press("/")
        self.page.get_by_role("combobox", name="문서 검색", exact=True).fill("")
        self.type_slow("연체")
        option = self.page.get_by_role("option", name="‘연체’ 전체 결과 보기", exact=True)
        self.click(option)
        self.say(5)
        scope = self.page.get_by_role("search").get_by_role("combobox", name="저장소", exact=True)
        self.move_to(scope)
        scope.select_option("")
        self.click(self.button("검색"))
        self.page.get_by_role("search").wait_for(state="visible")

    def document(self) -> None:
        # 장면 전환도 검색 결과에서 문서를 눌러 이어 간다.
        hit = self.page.get_by_role("link", name="설계/시스템-구성.md", exact=True).first
        self.click(hit)
        self.page.get_by_role("heading", name="도서 대여 시스템 구성", exact=True).wait_for()
        self.ready()
        self.say(0)
        self.move_to(self.page.locator(".shell-header").get_by_role("combobox", name="테마", exact=True))
        self.say(1)
        self.select("테마", "github-dark")
        self.say(2)
        self.select("테마", "minimal")
        self.say(3)
        self.click(self.page.get_by_text("내려받기", exact=True))
        self.say(4)
        self.download(self.link("HTML"), "시스템-구성.html")
        self.say(5)
        self.page.keyboard.press("Escape")
        self.select("테마", "github-light")
        self.select("브랜치", "draft")
        self.tree_document("설계", "시스템-구성.md")
        self.say(6)
        change = self.page.get_by_text("검토 중인 변경안:", exact=False)
        change.wait_for(state="visible")
        self.move_to(change)
        self.say(7)
        self.page.keyboard.press("?")
        self.page.get_by_role("dialog", name="단축키", exact=True).wait_for(state="visible")
        self.raise_overlay()
        self.wander_until(self.voice_free)
        self.page.keyboard.press("Escape")

    def active_card(self) -> Locator:
        memo_id = self.page.locator("textarea:focus").evaluate(
            "element => element.closest('.meeting-card').dataset.memoId")
        return self.page.locator(f'.meeting-card[data-memo-id="{memo_id}"]')

    def memo_text(self, card: Locator, text: str) -> None:
        area = card.get_by_role("textbox", name="메모 내용", exact=True)
        self.click(area)
        self.type_slow(text)
        area.blur()
        self.wait(0.25)

    def select_passage(self) -> None:
        text = self.page.get_by_text("대여 기간을 유지하고 반납 안내를 보강합니다.", exact=True)
        text.scroll_into_view_if_needed()
        # 실제 글자 Range의 시작·끝 좌표를 써서 줄바꿈이 생겨도 끝까지 드래그한다.
        points = text.evaluate("""element => {
          const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
          const nodes = []; while (walker.nextNode()) if (walker.currentNode.length) nodes.push(walker.currentNode);
          const a = document.createRange(), b = document.createRange();
          a.setStart(nodes[0], 0); a.setEnd(nodes[0], 1);
          const last = nodes[nodes.length - 1];
          b.setStart(last, last.length - 1); b.setEnd(last, last.length);
          const p = a.getBoundingClientRect(), q = b.getBoundingClientRect();
          return [p.left + 1, p.top + p.height / 2, q.right - 1, q.top + q.height / 2];
        }""")
        self.move(points[0], points[1])
        self.page.mouse.down()
        self.move(points[2], points[3], 1.1)
        self.page.mouse.up()
        self.button("+ 선택 영역에 메모").wait_for(state="visible")

    def meeting(self) -> None:
        self.select("브랜치", "main")
        self.tree_document("회의", "2026-11-주간-회의.md")
        self.page.get_by_text("진행 중", exact=False).first.wait_for()
        self.link("회의 모드로 열기").wait_for(state="visible")
        self.ready()
        self.say(0)
        self.move_to(self.page.locator(".meeting-info"))
        self.say(1)
        self.click(self.link("회의 모드로 열기"))
        guide = self.page.get_by_role("region", name="회의 모드 안내", exact=True)
        guide.wait_for(state="visible")
        self.say(2)
        self.move_to(guide)
        self.wander_until(self.voice_free)
        self.click(guide.get_by_role("button", name="닫기", exact=True))
        self.say(3)
        self.click(self.page.get_by_role("button", name="이 부분에 메모 추가", exact=True).first)
        self.memo_text(self.active_card(), "반납 안내 문구를 확인합니다.")
        # 다음 문장 드래그가 패널에 가려지지 않도록 닫는다.
        self.click(self.button("패널 닫기"))
        self.say(4)
        self.select_passage()
        self.click(self.button("+ 선택 영역에 메모"))
        decision = self.active_card()
        self.say(5)
        decision.get_by_role("combobox", name="메모 종류", exact=True).select_option("decision")
        self.memo_text(decision, "대여 기간은 현행대로 유지합니다.")
        self.say(6)
        self.click(self.button("+ 전체 메모"))
        action = self.active_card()
        action.get_by_role("combobox", name="메모 종류", exact=True).select_option("action")
        self.memo_text(action, "알림 발송 결과를 점검합니다.")
        owner = action.get_by_role("textbox", name="담당자", exact=True)
        self.click(owner)
        self.type_slow("운영 담당")
        due = action.get_by_label("기한", exact=True)
        self.move_to(due)
        due.fill((date.today() + timedelta(days=7)).isoformat())
        due.blur()
        panel = self.page.get_by_role("complementary", name="회의 메모", exact=True)
        self.say(7)
        self.click(panel.get_by_role("tab", name="회의 정리", exact=True))
        self.say(8)
        self.download(panel.get_by_role("button", name=".md로 내보내기", exact=True), "주간-회의.md")

    def present(self) -> None:
        self.click(self.link("회의 모드 나가기"))
        self.click(self.link("발표하기"))
        self.page.locator("body.page-present").wait_for()
        self.button("메모 보기(N)").wait_for(state="visible")
        self.ready()
        self.say(0)
        self.say(1)
        self.move_to(self.page.locator(".present-nav"))
        self.say(2)
        for key in ("ArrowRight", "ArrowRight", "Space"):
            self.page.keyboard.press(key)
            self.wait(0.65)
        self.say(3)
        self.click(self.button("메모 보기(N)"))
        panel = self.page.get_by_role("complementary", name="회의 메모", exact=True)
        panel.wait_for(state="visible")
        self.move_to(panel)
        self.say(4)
        self.page.keyboard.press("Escape")
        self.wait(0.6)
        with self.page.expect_navigation(wait_until="load"):
            self.page.keyboard.press("Escape")
        self.link("발표하기").wait_for(state="visible")

    def evidence(self) -> None:
        self.section("대여 기간 검토")
        self.link("대여 기간 조항").wait_for(state="visible")
        self.ready()
        self.say(0)
        self.say(1)
        self.move_to(self.link("대여 기간 조항"))
        self.page.get_by_role("tooltip").wait_for(state="visible")
        self.say(2)
        self.page.keyboard.press("Escape")
        self.click(self.link("연체 처리 조항"))
        collection = self.page.get_by_role("region", name="근거 모음", exact=True)
        entry = collection.get_by_role("article").nth(1)
        entry.wait_for(state="visible")
        self.say(3)
        cited = entry.get_by_role("link", name="연체 알림 개선", exact=True)
        self.move_to(cited)
        self.say(4)
        self.click(cited)
        self.say(5)
        self.click(self.link("연체 처리 조항"))
        self.click(entry.get_by_role("link", name="원문 열기", exact=True))
        self.page.get_by_role("heading", name="도서 대여 규칙", exact=True).wait_for()

    def preview(self) -> None:
        button = self.button("슬라이드 미리보기")
        self.move_to(button)
        with self.context.expect_page() as pending:
            self.click(button)
        popup = pending.value
        popup.set_default_timeout(60000)
        popup.wait_for_function("""() => document.querySelector('svg[data-marpit-svg]')
          && [...document.querySelectorAll('section')].some(s => s.getBoundingClientRect().width > 0)""")
        popup.evaluate("() => document.fonts.ready")
        # 미리보기의 document.write가 초기 겹침을 지우므로 렌더링 뒤 다시 넣는다.
        popup.evaluate(OVERLAY)
        self.stop_cast()
        self.switch_after = self.frames[-1]["t"] if self.frames else None
        self.page = popup
        self.page.bring_to_front()
        self.start_cast()
        self.move(640, 360, 0.4)
        deadline = time.monotonic() + 5
        while self.switch_after is not None and time.monotonic() < deadline:
            self.now()
            self.wait(0.05)
        if self.switch_after is not None:
            raise RuntimeError("새 탭의 첫 프레임이 도착하지 않았습니다")

    def slides(self) -> None:
        self.tree_document("발표", "서비스-소개.md")
        self.button("슬라이드 미리보기").wait_for(state="visible")
        self.button("PDF 만들기").wait_for(state="visible")
        self.ready()
        self.say(0)
        self.say(1)
        self.move_to(self.button("슬라이드 미리보기"))
        self.wait(0.5)
        self.move_to(self.button("PDF 만들기"))
        self.say(2)
        # PDF를 먼저 내려받고 미리보기 새 탭에서 녹화를 끝낸다.
        self.download(self.button("PDF 만들기"), "서비스-소개.pdf")
        self.say(3)
        self.preview()
        self.say(4)
        for key in ("ArrowRight", "ArrowRight"):
            self.page.keyboard.press(key)
            self.wait(0.8)

    def outro(self) -> None:
        self.page.wait_for_function("Boolean(document.querySelector('svg[data-marpit-svg]'))")
        self.ready()
        self.say(0)
        for key in ("ArrowRight", "ArrowRight"):
            self.page.keyboard.press(key)
            self.wait(0.8)
        self.say(1)
        self.say(2)
        self.move(640, 360, 1.2)
        self.say(3)

    def run(self, scenes: list[dict]) -> None:
        response = self.page.goto(self.base_url + "/view/", wait_until="load")
        if response is None or response.status != 200:
            raise RuntimeError("저장소 목록을 열지 못했습니다")
        self.raise_overlay()
        self.start_cast()
        self.move(620, 350, 0.35)
        for scene in scenes:
            self.scene = scene["key"]
            self.scene_marks.append({"scene": self.scene, "title": scene["title"], "t": self.now()})
            print(f"장면: {scene['title']} ({self.now():.1f}초)", flush=True)
            getattr(self, self.scene)()
            self.wander_until(self.voice_free + TAIL)
        end = self.now()
        self.stop_cast()
        if self.frame_error:
            raise RuntimeError(self.frame_error)
        if not self.frames:
            raise RuntimeError("녹화 프레임이 없습니다")
        marks = {"end": end, "scenes": self.scene_marks, "lines": self.lines, "frames": self.frames}
        (self.out / "marks.json").write_text(json.dumps(marks, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"녹화 완료: {end:.1f}초, 프레임 {len(self.frames)}개", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:18090", help="Viewer 서버의 기본 주소")
    parser.add_argument("--out", type=Path, default=HERE / "out", help="결과 폴더")
    args = parser.parse_args()
    out = args.out.expanduser().resolve()
    scenes = json.loads((HERE / "scenes.json").read_text(encoding="utf-8"))["scenes"]
    voice = load_voice(out, scenes)
    out.mkdir(parents=True, exist_ok=True)
    # 실패한 녹화에서 이전 marks와 새 프레임이 섞이지 않도록 시작 전에 지운다.
    (out / "marks.json").unlink(missing_ok=True)
    shutil.rmtree(out / "frames", ignore_errors=True)
    (out / "frames").mkdir()
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(
            viewport=SIZE, device_scale_factor=1.5, locale="ko-KR", timezone_id="Asia/Seoul",
            accept_downloads=True, bypass_csp=True,
        )
        recorder = Recorder(context, args.base_url, out, voice)
        try:
            recorder.run(scenes)
        except Exception:
            try:
                recorder.page.screenshot(path=out / "실패.png")
            except (Error, OSError):
                pass
            raise
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    try:
        main()
    except Error as error:
        detail = "\n".join(str(error).splitlines()[:6])
        print(f"오류: Chrome 조작에 실패했습니다. 출력 폴더의 실패.png를 확인하세요.\n{detail}", file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        print(f"오류: {error}", file=sys.stderr)
        sys.exit(1)

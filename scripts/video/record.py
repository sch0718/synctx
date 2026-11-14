"""실제 Viewer를 조작해 메뉴별 영상을 녹화하고 배포용 MP4로 변환한다."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import Browser, BrowserContext, Locator, Page, sync_playwright

# README 「메뉴별 도움말」 표의 메뉴 이름과 같게 둔다(영상 제목에도 쓴다).
MENUS = {
    "browse": "저장소·문서 찾아가기", "search": "문서 검색", "document": "문서 보기·내려받기",
    "meeting": "회의 모드", "present": "발표 화면", "evidence": "근거 미리보기·근거 모음",
    "slides": "슬라이드(Marp)",
}
SYSTEM = "설계/시스템-구성.md"
MEETING = "회의/2026-11-주간-회의.md"
SLIDES = "발표/서비스-소개.md"
SIZE = {"width": 1920, "height": 1080}

# 모달을 연 뒤에도 자막과 커서를 다시 최상위 층으로 올린다.
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
      #caption {position:absolute;bottom:52px;left:50%;transform:translateX(-50%);
        padding:15px 28px;border-radius:12px;background:rgba(0,0,0,.82);color:white;
        font:500 36px/1.4 'Noto Sans KR','Apple SD Gothic Neo',sans-serif;
        white-space:nowrap;max-width:calc(100vw - 80px);text-align:center;}
      #caption:empty {display:none;}
      #cursor {position:absolute;left:0;top:0;width:30px;height:38px;
        filter:drop-shadow(1px 2px 2px #333);transform:translate(960px,540px);}
      #ring {position:absolute;left:0;top:0;width:48px;height:48px;border:3px solid #ffba37;
        border-radius:50%;opacity:0;}
    </style><div id="caption"></div>
    <svg id="cursor" viewBox="0 0 30 38"><path d="M2 2L2 29L9 23L15 35L21 32L15 21L26 21Z"
      fill="white" stroke="#18212d" stroke-width="2"/></svg><div id="ring"></div>`;
    document.body.appendChild(layer);
    const label = shadow.getElementById('caption');
    const cursor = shadow.getElementById('cursor');
    const ring = shadow.getElementById('ring');
    const raise = () => {
      // 문서가 바뀌는 중이면 popover 호출이 실패할 수 있다. 다음 자막에서 다시 올린다.
      try {
        if (layer.matches(':popover-open')) layer.hidePopover();
        layer.showPopover();
      } catch (_) {}
    };
    window.videoOverlay = {caption(text) {
      label.textContent = text;
      try { sessionStorage.setItem('video-caption', text); } catch (_) {}
      raise();
    }};
    try { label.textContent = sessionStorage.getItem('video-caption') || ''; } catch (_) {}
    document.addEventListener('mousemove', e => {
      cursor.style.transform = `translate(${e.clientX}px,${e.clientY}px)`;
    }, true);
    document.addEventListener('mousedown', e => {
      ring.style.left = `${e.clientX - 24}px`; ring.style.top = `${e.clientY - 24}px`;
      ring.animate([{opacity:1,transform:'scale(.3)'},{opacity:0,transform:'scale(1.8)'}],
        {duration:550});
    }, true);
    raise();
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', install, {once:true});
  } else install();
})()"""


class Recorder:
    def __init__(self, context: BrowserContext, base_url: str, out: Path, menu: str):
        self.context = context
        self.base_url = base_url.rstrip("/")
        self.out = out
        self.menu = menu
        self.born: dict[Page, float] = {}
        context.on("page", lambda page: self.born.setdefault(page, time.monotonic()))
        context.add_init_script(OVERLAY)
        self.page = context.new_page()
        self.origin = self.page
        self.popup: Page | None = None
        self.splice: dict[str, float] = {}
        self.page.set_default_timeout(15000)

    def show(self, text: str) -> None:
        self.page.wait_for_function("Boolean(window.videoOverlay)")
        self.page.evaluate("text => window.videoOverlay.caption(text)", text)

    def caption(self, text: str, hold_ms: int = 3000) -> None:
        self.show(text)
        self.page.wait_for_timeout(hold_ms)

    def step(self, text: str, action: Callable[[], object] | None = None, hold_ms: int = 3000) -> None:
        """조작 시간을 포함해 각 자막을 지정한 시간 이상 보여 준다."""
        started = time.monotonic()
        self.show(text)
        if action:
            action()
        # 문서 이동으로 겹침 층이 바뀌어도 같은 자막을 이어서 보여 준다.
        self.show(text)
        self.page.wait_for_timeout(max(0, hold_ms - (time.monotonic() - started) * 1000))

    def move_to(self, locator: Locator) -> None:
        locator.wait_for(state="visible")
        locator.scroll_into_view_if_needed()
        box = locator.bounding_box()
        if box is None:
            raise RuntimeError("조작할 요소의 화면 위치를 찾지 못했습니다")
        self.page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=25)

    def click(self, locator: Locator) -> None:
        self.move_to(locator)
        locator.click()

    def type_slow(self, text: str) -> None:
        self.page.keyboard.type(text, delay=80)

    def press(self, key: str, text: str, hold_ms: int = 3000, navigation: bool = False) -> None:
        label = {"Meta+k": "⌘K", "ArrowDown": "↓", "ArrowRight": "→",
                 "Space": "스페이스", "Escape": "Esc", "Enter": "Enter"}.get(key, key)
        def perform() -> None:
            if navigation:
                with self.page.expect_navigation(wait_until="load"):
                    self.page.keyboard.press(key)
            else:
                self.page.keyboard.press(key)

        self.step(f"{text}  [{label}]", perform, hold_ms)

    def open(self, path: str = "") -> None:
        suffix = f"demo/{quote(path, safe='/')}" if path else ""
        response = self.page.goto(f"{self.base_url}/view/{suffix}", wait_until="load")
        if response is None or response.status != 200:
            raise RuntimeError(f"시연 화면을 열지 못했습니다: {path or '저장소 목록'}")
        self.page.wait_for_timeout(500)

    def intro(self, path: str = "") -> None:
        self.open(path)
        self.caption(f"synctx 사용법 — {MENUS[self.menu]}")

    def finish(self) -> None:
        self.caption(f"도움말: docs/help/{self.menu}.md")

    def button(self, name: str) -> Locator:
        return self.page.get_by_role("button", name=name, exact=True)

    def link(self, name: str) -> Locator:
        return self.page.get_by_role("link", name=name, exact=True)

    def section(self, name: str) -> None:
        self.page.get_by_role("heading", name=name, exact=True).evaluate(
            "element => element.scrollIntoView({block:'start', behavior:'instant'})")

    def select(self, label: str, value: str) -> None:
        # 넓은 화면에서 보이는 상단 컨트롤만 사용한다.
        control = self.page.locator(".shell-header").get_by_role("combobox", name=label, exact=True)
        self.move_to(control)
        with self.page.expect_navigation(wait_until="load"):
            control.select_option(value)

    def draft_document(self) -> None:
        # 브랜치 선택은 저장소 첫 화면으로 이동하므로 같은 문서를 트리에서 다시 연다.
        self.select("브랜치", "draft")
        self.click(self.button("설계/"))
        self.click(self.page.locator("#file-tree").get_by_role("link", name="시스템-구성.md", exact=True))

    def download(self, locator: Locator, filename: str) -> None:
        with self.page.expect_download(timeout=120000) as pending:
            self.click(locator)
        downloaded = pending.value
        failure = downloaded.failure()
        if failure:
            raise RuntimeError(f"내려받지 못했습니다: {failure}")
        target = self.out / "downloads" / self.menu / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        downloaded.save_as(target)

    def browse(self) -> None:
        self.intro()
        self.caption("저장소 목록에서 읽을 문서를 고릅니다")
        self.step("예시 문서 저장소를 엽니다", lambda: self.click(
            self.page.get_by_role("link").filter(has=self.page.get_by_text("예시 문서", exact=True))))
        self.step("파일 트리에서 설계 폴더를 펼칩니다", lambda: self.click(self.button("설계/")))
        self.step("시스템 구성 문서를 엽니다", lambda: self.click(
            self.page.locator("#file-tree").get_by_role("link", name="시스템-구성.md", exact=True)))
        self.step("표로 구성 요소와 역할을 확인합니다", lambda: self.section("구성 요소"))
        self.step("처리 흐름은 문서 안의 그림으로 확인합니다", lambda: self.section("처리 흐름"))
        self.page.locator(".mermaid svg").wait_for(state="visible")
        self.step("코드도 강조된 글자로 읽을 수 있습니다", lambda: self.section("대여 처리 예시"))
        self.step("설정 예시를 아래로 이어서 읽습니다", lambda: self.section("설정 예시"))
        toc = self.page.get_by_role("complementary", name="이 문서의 목차")
        self.step("오른쪽 이 문서 목차에서 절로 이동합니다", lambda: self.click(
            toc.get_by_role("link", name="서비스 개요", exact=True)))
        self.press("t", "목차 첫 항목으로 초점을 옮깁니다")
        self.finish()

    def search(self) -> None:
        self.intro(SYSTEM)
        self.press("Meta+k", "어느 문서에서든 검색을 엽니다")
        self.step("찾을 단어를 입력합니다", lambda: self.type_slow("대여"))
        results = self.page.get_by_role("listbox", name="검색 결과")
        results.get_by_role("group", name="파일", exact=True).wait_for()
        self.caption("파일 이름이 맞는 문서를 먼저 보여 줍니다")
        results.get_by_role("group", name="본문", exact=True).wait_for()
        self.caption("본문에 같은 단어가 있는 위치도 보여 줍니다")
        self.press("ArrowDown", "아래 화살표로 결과를 고릅니다")
        self.press("ArrowDown", "다음 결과로 이동합니다")
        self.press("Enter", "선택한 문서를 엽니다", navigation=True)
        self.press("/", "슬래시로 검색을 다시 엽니다")

        def overdue() -> None:
            self.page.get_by_role("combobox", name="문서 검색").fill("")
            self.type_slow("연체")
            results.get_by_role("group", name="본문", exact=True).wait_for()

        self.step("이번에는 연체가 나오는 곳을 찾습니다", overdue)
        self.step("전체 결과 보기로 검색 화면을 엽니다", lambda: self.click(
            results.get_by_role("option", name="‘연체’ 전체 결과 보기", exact=True)))
        scope = self.page.get_by_role("search").get_by_role("combobox", name="저장소", exact=True)
        self.step("모든 저장소를 선택하면 검색 범위를 넓힙니다", lambda: scope.select_option(""))
        self.step("검색 결과에서 문서와 해당 줄을 확인합니다", lambda: self.click(self.button("검색")))
        self.finish()

    def document(self) -> None:
        self.intro(SYSTEM)
        self.caption("상단에서 문서 표시와 내려받기를 바꿉니다")
        self.step("테마를 어두운 화면으로 바꿉니다", lambda: self.select("테마", "github-dark"))
        self.step("간결한 테마로도 읽을 수 있습니다", lambda: self.select("테마", "minimal"))
        # 선택 목록을 펼치지 않아 시연에 필요 없는 다른 테마 이름은 화면에 담지 않는다.
        self.step("내려받기 메뉴를 엽니다", lambda: self.click(self.page.get_by_text("내려받기", exact=True)))
        self.caption("원본·HTML·PDF 중 필요한 형식을 고릅니다")
        self.step("HTML 파일로 저장합니다", lambda: self.download(self.link("HTML"), "시스템-구성.html"))
        self.press("Escape", "내려받기 메뉴를 닫습니다")
        self.step("draft로 바꾼 뒤 같은 문서를 다시 엽니다", self.draft_document)
        self.page.get_by_text("검토 중인 변경안:", exact=False).wait_for()
        self.caption("대여 기간과 한도 변경안을 비교할 수 있습니다")
        self.press("?", "물음표를 누르면 단축키 안내가 열립니다")
        self.page.get_by_role("dialog", name="단축키", exact=True).wait_for()
        self.press("Escape", "안내를 닫고 문서로 돌아옵니다")
        self.finish()

    def memo_text(self, text: str) -> None:
        # 새 메모를 만들면 실제 회의 코드가 내용 입력칸으로 초점을 옮긴다.
        active = self.page.locator("textarea:focus")
        active.wait_for(state="visible")
        self.type_slow(text)
        active.blur()

    def active_card(self) -> Locator:
        memo_id = self.page.locator("textarea:focus").evaluate(
            "element => element.closest('.meeting-card').dataset.memoId")
        return self.page.locator(f'.meeting-card[data-memo-id="{memo_id}"]')

    def edit_memo(self, card: Locator, text: str) -> None:
        self.click(card.get_by_role("textbox", name="메모 내용", exact=True))
        self.type_slow(text)
        card.get_by_role("textbox", name="메모 내용", exact=True).blur()

    def meeting(self) -> None:
        self.intro(MEETING)
        self.page.get_by_text("진행 중", exact=False).first.wait_for()
        self.caption("머리 카드에서 시간과 진행 중 상태를 확인합니다")
        self.step("회의 모드로 들어갑니다", lambda: self.click(self.link("회의 모드로 열기")))
        guide = self.page.get_by_role("region", name="회의 모드 안내", exact=True)
        guide.wait_for(state="visible")
        self.step("메모 저장 안내를 읽고 닫습니다", lambda: self.click(
            guide.get_by_role("button", name="닫기", exact=True)))
        self.step("문단 옆 더하기로 일반 메모를 만듭니다", lambda: self.click(
            self.page.get_by_role("button", name="이 부분에 메모 추가", exact=True).first))
        self.step("확인할 내용을 짧게 적습니다", lambda: self.memo_text("반납 안내 문구를 확인합니다."))
        self.step("본문을 드래그해 결정할 내용을 고릅니다", self.select_passage)
        self.step("선택 영역에 연결된 메모를 만듭니다", lambda: self.click(self.button("+ 선택 영역에 메모")))
        focused_card = self.active_card()
        self.step("메모 종류를 결정으로 바꿉니다", lambda: focused_card.get_by_role(
            "combobox", name="메모 종류", exact=True).select_option("decision"))
        self.step("결정한 내용을 남깁니다", lambda: self.edit_memo(focused_card, "대여 기간은 현행대로 유지합니다."))
        self.step("다음 작업은 전체 메모로 추가합니다", lambda: self.click(self.button("+ 전체 메모")))
        action_card = self.active_card()
        self.step("액션으로 바꾸고 할 일을 적습니다", lambda: self.write_action(action_card))
        panel = self.page.get_by_role("complementary", name="회의 메모", exact=True)
        self.step("담당자와 기한을 지정합니다", lambda: self.action_fields(panel))
        self.step("회의 정리에 결정과 액션을 모아 봅니다", lambda: self.click(
            panel.get_by_role("tab", name="회의 정리", exact=True)))
        self.step("마크다운으로 내보내 함께 확인합니다", lambda: self.download(
            panel.get_by_role("button", name=".md로 내보내기", exact=True), "주간-회의.md"))
        self.finish()

    def select_passage(self) -> None:
        text = self.page.get_by_text("대여 기간을 유지하고 반납 안내를 보강합니다.", exact=True)
        text.scroll_into_view_if_needed()
        # 실제 드래그로 선택하고 mouseup을 보내 제품의 선택 영역 단추를 연다.
        box = text.bounding_box()
        if box is None:
            raise RuntimeError("선택할 문장의 위치를 찾지 못했습니다")
        y = box["y"] + box["height"] / 2
        self.page.mouse.move(box["x"] + 2, y, steps=25)
        self.page.mouse.down()
        self.page.mouse.move(box["x"] + box["width"] - 2, y, steps=25)
        self.page.mouse.up()
        self.button("+ 선택 영역에 메모").wait_for(state="visible")

    def write_action(self, card: Locator) -> None:
        card.get_by_role("combobox", name="메모 종류", exact=True).select_option("action")
        self.click(card.get_by_role("textbox", name="메모 내용", exact=True))
        self.type_slow("알림 발송 결과를 점검합니다.")

    def action_fields(self, panel: Locator) -> None:
        card = panel.locator(".meeting-card--action")
        self.click(card.get_by_role("textbox", name="담당자", exact=True))
        self.type_slow("운영 담당")
        due = card.get_by_label("기한", exact=True)
        due.fill((date.today() + timedelta(days=7)).isoformat())
        due.blur()

    def present(self) -> None:
        self.intro(MEETING)
        self.caption("회의 문서는 안건별로 발표할 수 있습니다")
        self.step("발표하기를 눌러 발표 화면을 엽니다", lambda: self.click(self.link("발표하기")))
        self.page.locator("body.page-present").wait_for()
        self.caption("문서의 큰 제목마다 장이 나뉩니다")
        self.press("ArrowRight", "오른쪽 화살표로 다음 안건을 엽니다")
        self.press("ArrowRight", "다음 장에서 논의를 이어 갑니다")
        self.press("Space", "스페이스로도 장을 넘길 수 있습니다")
        self.step("메모 보기로 내 메모 패널을 곁에 엽니다", lambda: self.click(self.button("메모 보기(N)")))
        self.caption("회의 모드에서 적은 메모가 여기에 보입니다")
        self.press("Escape", "첫 번째 Esc로 메모 패널을 닫습니다")
        self.press("Escape", "다시 Esc를 눌러 발표를 끝냅니다", navigation=True)
        self.link("발표하기").wait_for(state="visible")
        self.caption("원래 문서 화면으로 돌아왔습니다")
        self.finish()

    def evidence(self) -> None:
        self.intro(MEETING)
        self.caption("회의 안건에서 규칙의 근거를 바로 확인합니다")
        self.step("대여 기간 조항에 마우스를 올립니다", lambda: self.move_to(self.link("대여 기간 조항")))
        self.page.get_by_role("tooltip").wait_for(state="visible")
        self.caption("말풍선에서 규칙의 제목과 발췌를 읽습니다")
        self.press("Escape", "Esc로 말풍선을 닫습니다")
        self.step("이번에는 연체 처리 조항을 누릅니다", lambda: self.click(self.link("연체 처리 조항")))
        collection = self.page.get_by_role("region", name="근거 모음", exact=True)
        entry = collection.get_by_role("article").nth(1)
        self.caption("문서 끝의 근거 모음으로 이동합니다")
        self.caption("인용한 곳에서 이 근거를 쓴 안건을 찾습니다")
        self.step("인용한 곳을 누르면 해당 안건으로 돌아갑니다", lambda: self.click(
            entry.get_by_role("link", name="연체 알림 개선", exact=True)))
        self.step("근거 링크를 다시 눌러 모음으로 갑니다", lambda: self.click(self.link("연체 처리 조항")))
        self.step("원문 열기로 규칙 전체를 확인합니다", lambda: self.click(
            entry.get_by_role("link", name="원문 열기", exact=True)))
        self.page.get_by_role("heading", name="도서 대여 규칙", exact=True).wait_for()
        self.finish()

    def preview(self) -> None:
        button = self.button("슬라이드 미리보기")
        self.move_to(button)
        self.splice["click"] = time.monotonic() - self.born[self.origin]
        with self.context.expect_page() as pending:
            button.click()
        self.popup = pending.value
        self.popup.set_default_timeout(60000)
        self.popup.wait_for_function("""() => document.querySelector('svg[data-marpit-svg]')
          && [...document.querySelectorAll('section')].some(s => s.getBoundingClientRect().width > 0)""")
        self.popup.evaluate("() => document.fonts.ready")
        # document.write가 초기 겹침 층을 지우므로 렌더링 뒤 다시 넣는다.
        self.popup.evaluate(OVERLAY)
        self.page = self.popup
        self.show("새 탭에서 열립니다")
        self.splice["ready"] = time.monotonic() - self.born[self.popup]
        self.caption("새 탭에서 열립니다")

    def slide_pdf(self) -> None:
        with self.page.expect_download(timeout=120000) as pending:
            self.click(self.button("PDF 만들기"))
            self.caption("PDF로 변환하는 동안 진행 상태를 보여 줍니다")
        downloaded = pending.value
        if downloaded.failure():
            raise RuntimeError("슬라이드 PDF를 내려받지 못했습니다")
        target = self.out / "downloads" / self.menu / "서비스-소개.pdf"
        target.parent.mkdir(parents=True, exist_ok=True)
        downloaded.save_as(target)

    def slides(self) -> None:
        # 미리보기 탭을 닫고 원래 탭으로 돌아오면 헤드리스 녹화 크기가 바뀌어 화면 아래가 잘린다.
        # 그래서 PDF를 먼저 만들고, 미리보기 탭에서 영상을 끝낸다.
        self.intro(SLIDES)
        self.caption("marp: true 문서에는 슬라이드 단추가 생깁니다")
        self.step("PDF 만들기로 배포할 파일을 만듭니다", self.slide_pdf)
        self.caption("내려받기가 끝나면 PDF 파일을 사용할 수 있습니다")
        self.step("슬라이드 미리보기를 누릅니다", self.preview)
        self.caption("첫 장에서 서비스 소개를 시작합니다")
        self.press("ArrowRight", "오른쪽 화살표로 이용 흐름을 봅니다")
        self.press("ArrowRight", "대여 기준도 표로 보여 줍니다")
        self.press("Space", "스페이스로 다음 장을 넘깁니다")
        self.finish()

def probe(path: Path) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)


def encode(source: Path, popup: Path | None, splice: dict[str, float], target: Path) -> None:
    command = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-y", "-i", str(source)]
    if popup is None:
        command += ["-vf", "trim=start=0.5,setpts=PTS-STARTPTS"]
    else:
        command += ["-i", str(popup), "-filter_complex", (
            f"[0:v]trim=start=0.5:end={splice['click']:.6f},setpts=PTS-STARTPTS[a];"
            f"[1:v]trim=start={splice['ready']:.6f},setpts=PTS-STARTPTS[b];"
            "[a][b]concat=n=2:v=1:a=0[v]"), "-map", "[v]"]
    command += ["-an", "-c:v", "libx264", "-crf", "18", "-preset", "slow",
                "-pix_fmt", "yuv420p", "-r", "30", "-movflags", "+faststart", str(target)]
    subprocess.run(command, check=True)
    metadata = probe(target)
    video = next(stream for stream in metadata["streams"] if stream["codec_type"] == "video")
    if (video["width"], video["height"]) != (1920, 1080):
        raise RuntimeError(f"영상 해상도가 예상과 다릅니다: {target}")
    duration = float(metadata["format"]["duration"])
    print(f"영상: {target} ({duration:.1f}초, 1920×1080)")
    if not 30 <= duration <= 60:
        print(f"경고: {target.name} 길이가 30~60초를 벗어났습니다. 해당 메뉴의 자막 시간을 조정하세요.")


def record(browser: Browser, base_url: str, out: Path, menu: str) -> None:
    raw = out / "raw" / menu
    raw.mkdir(parents=True, exist_ok=True)
    context = browser.new_context(
        viewport=SIZE, record_video_size=SIZE, record_video_dir=raw,
        accept_downloads=True, locale="ko-KR", device_scale_factor=1,
        # Viewer CSP가 unsafe-eval을 막아 wait_for_function이 돌지 않는다. 화면 내용은 그대로다.
        bypass_csp=True,
    )
    try:
        recorder = Recorder(context, base_url, out, menu)
        getattr(recorder, menu)()
    finally:
        context.close()
    source = raw / f"{menu}.webm"
    popup = raw / "preview.webm" if recorder.popup else None
    for page, target in [(recorder.origin, source), (recorder.popup, popup)]:
        if page is not None and target is not None and page.video is not None:
            original = page.video.path()
            page.video.save_as(target)
            if original != target:
                original.unlink(missing_ok=True)
    if not source.is_file():
        raise RuntimeError("녹화 원본을 찾지 못했습니다")
    if popup:
        (raw / "timing.json").write_text(json.dumps(recorder.splice, indent=2) + "\n", encoding="utf-8")
    encode(source, popup, recorder.splice, out / f"{menu}.mp4")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--menu", action="append", choices=MENUS, help="녹화할 메뉴, 여러 번 지정 가능")
    parser.add_argument("--base-url", default="http://127.0.0.1:18090", help="Viewer 서버의 기본 주소")
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parent / "out", help="결과 폴더")
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(channel="chrome", headless=True)
        try:
            for menu in dict.fromkeys(args.menu or MENUS):
                print(f"녹화 중: {MENUS[menu]}", flush=True)
                record(browser, args.base_url, out, menu)
        finally:
            browser.close()


if __name__ == "__main__":
    main()

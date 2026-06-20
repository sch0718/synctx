#!/usr/bin/env python3
"""범용 HTML -> PDF 변환 스크립트 (Chrome DevTools Protocol 사용)."""

import argparse
import base64
import html as html_lib
import json
import os
import platform
import re
import subprocess
import time
from datetime import date
from pathlib import Path

import websocket


def _detect_chrome_path():
    system = platform.system()
    if system == "Darwin":
        return "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if system == "Windows":
        candidates = [
            Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
        ]
        for p in candidates:
            if p.exists():
                return str(p)
        return str(candidates[0])
    # Linux
    for name in ("google-chrome", "google-chrome-stable", "chromium-browser", "chromium"):
        import shutil
        found = shutil.which(name)
        if found:
            return found
    return "google-chrome"


DEFAULT_CHROME_PATH = _detect_chrome_path()

CONTENT_CSS_TEMPLATE = """
* { margin: 0; padding: 0; box-sizing: border-box; }
html {
  -webkit-print-color-adjust: exact !important;
  print-color-adjust: exact !important;
}
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans", Helvetica, Arial, sans-serif;
  font-size: 14px; line-height: 1.5; color: #1f2328; background: #fff;
}
.__CONTENT_CLASS__ {
  max-width: 980px; margin: 0 auto; padding: 20px 45px;
}
.__CONTENT_CLASS__ h1, .__CONTENT_CLASS__ h2, .__CONTENT_CLASS__ h3, .__CONTENT_CLASS__ h4, .__CONTENT_CLASS__ h5, .__CONTENT_CLASS__ h6 {
  margin-top: 24px; margin-bottom: 16px; font-weight: 600; line-height: 1.25;
}
.__CONTENT_CLASS__ h1 { font-size: 2em; padding-bottom: .3em; border-bottom: 1px solid #d1d9e0b3; }
.__CONTENT_CLASS__ h2 { font-size: 1.5em; padding-bottom: .3em; border-bottom: 1px solid #d1d9e0b3; }
.__CONTENT_CLASS__ h3 { font-size: 1.25em; }
.__CONTENT_CLASS__ h4 { font-size: 1em; }
.__CONTENT_CLASS__ p { margin-top: 0; margin-bottom: 16px; font-size: 16px; line-height: 1.5; }
.__CONTENT_CLASS__ a { color: #0969da; text-decoration: none; }
.__CONTENT_CLASS__ strong { font-weight: 600; }
.__CONTENT_CLASS__ blockquote { margin: 0 0 16px; padding: 0 1em; color: #656d76; border-left: .25em solid #d0d7de; }
.__CONTENT_CLASS__ blockquote > :first-child { margin-top: 0; }
.__CONTENT_CLASS__ blockquote > :last-child { margin-bottom: 0; }
.__CONTENT_CLASS__ ul, .__CONTENT_CLASS__ ol { margin-top: 0; margin-bottom: 16px; padding-left: 2em; font-size: 16px; }
.__CONTENT_CLASS__ ul ul, .__CONTENT_CLASS__ ul ol, .__CONTENT_CLASS__ ol ol, .__CONTENT_CLASS__ ol ul { margin-top: 0; margin-bottom: 0; }
.__CONTENT_CLASS__ li { margin-top: .25em; }
.__CONTENT_CLASS__ code {
  padding: .2em .4em; font-size: 85%; background: rgba(175,184,193,.2); border-radius: 6px;
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
}
.__CONTENT_CLASS__ pre {
  margin-top: 0; margin-bottom: 16px; padding: 16px; overflow: auto;
  font-size: 85%; line-height: 1.45; color: #1f2328; background: #f6f8fa; border-radius: 6px;
}
.__CONTENT_CLASS__ pre code { display: inline; padding: 0; background: transparent; border: 0; font-size: 100%; white-space: pre; }
.__CONTENT_CLASS__ table { border-spacing: 0; border-collapse: collapse; margin-bottom: 16px; width: 100%; font-size: 16px; }
.__CONTENT_CLASS__ table th { font-weight: 600; padding: 6px 13px; border: 1px solid #d0d7de; background: #f6f8fa; }
.__CONTENT_CLASS__ table td { padding: 6px 13px; border: 1px solid #d0d7de; }
.__CONTENT_CLASS__ table tr { background: #fff; border-top: 1px solid #d8dee4; }
.__CONTENT_CLASS__ table tr:nth-child(2n) { background: #f6f8fa; }
.__CONTENT_CLASS__ hr { height: .25em; margin: 24px 0; background: #d0d7de; border: 0; }
.__CONTENT_CLASS__ img { max-width: 100%; }
.__CONTENT_CLASS__ > *:first-child { margin-top: 0 !important; }
.section-divider { page-break-before: always; }
.__CONTENT_CLASS__ table { page-break-inside: auto; }
.__CONTENT_CLASS__ tr { page-break-inside: avoid; }
.__CONTENT_CLASS__ pre { page-break-inside: avoid; }
.__CONTENT_CLASS__ h1, .__CONTENT_CLASS__ h2, .__CONTENT_CLASS__ h3, .__CONTENT_CLASS__ h4 { page-break-after: avoid; }
.cover-page { page-break-after: always; }
@media print {
  * { -webkit-print-color-adjust: exact !important; print-color-adjust: exact !important; color-adjust: exact !important; }
}
"""


class ChromeCDP:
    """Chrome DevTools Protocol을 통해 HTML을 PDF로 변환."""

    def __init__(self, chrome_path, port=9222):
        self.chrome_path = chrome_path
        self.port = port
        self.proc = None
        self.ws = None
        self.msg_id = 0

    def start(self):
        import tempfile
        import urllib.request

        user_data_dir = os.path.join(tempfile.gettempdir(), "chrome-pdf-profile")
        self.proc = subprocess.Popen(
            [
                self.chrome_path,
                "--headless",
                "--disable-gpu",
                "--no-sandbox",
                f"--remote-debugging-port={self.port}",
                f"--user-data-dir={user_data_dir}",
                "--remote-allow-origins=*",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        url = f"http://127.0.0.1:{self.port}/json/version"
        for attempt in range(10):
            time.sleep(1)
            try:
                resp = urllib.request.urlopen(url)
                break
            except (ConnectionError, OSError):
                if attempt == 9:
                    raise RuntimeError(f"Chrome CDP에 연결할 수 없습니다 ({url})")
        info = json.loads(resp.read())
        self.ws = websocket.create_connection(info["webSocketDebuggerUrl"], timeout=120)

    def stop(self):
        if self.ws:
            self.ws.close()
        if self.proc:
            self.proc.terminate()
            self.proc.wait(timeout=5)

    def _send(self, method, params=None, session_id=None):
        self.msg_id += 1
        msg = {"id": self.msg_id, "method": method}
        if params:
            msg["params"] = params
        if session_id:
            msg["sessionId"] = session_id
        self.ws.send(json.dumps(msg))
        while True:
            response = json.loads(self.ws.recv())
            if response.get("id") == self.msg_id:
                return response

    def _wait_event(self, event_name, timeout=15):
        deadline = time.time() + timeout
        original_timeout = self.ws.gettimeout()
        self.ws.settimeout(1)
        try:
            while time.time() < deadline:
                try:
                    event = json.loads(self.ws.recv())
                    if event.get("method") == event_name:
                        return event
                except websocket.WebSocketTimeoutException:
                    pass
        finally:
            self.ws.settimeout(original_timeout)
        return None

    def html_to_pdf(self, html_path, pdf_path):
        file_url = html_path.as_uri()

        created = self._send("Target.createTarget", {"url": "about:blank"})
        target_id = created["result"]["targetId"]
        attached = self._send(
            "Target.attachToTarget",
            {"targetId": target_id, "flatten": True},
        )
        sid = attached["result"]["sessionId"]

        self._send("Page.enable", session_id=sid)
        self._send("Page.navigate", {"url": file_url}, session_id=sid)
        self._wait_event("Page.loadEventFired", timeout=20)
        time.sleep(1)

        printed = self._send(
            "Page.printToPDF",
            {
                "printBackground": True,
                "preferCSSPageSize": False,
                "displayHeaderFooter": False,
                "transferMode": "ReturnAsStream",
            },
            session_id=sid,
        )

        success = False
        if "result" in printed and "stream" in printed["result"]:
            stream_handle = printed["result"]["stream"]
            chunks = []
            while True:
                part = self._send(
                    "IO.read",
                    {"handle": stream_handle, "size": 1024 * 1024},
                    session_id=sid,
                )
                data = part["result"].get("data", "")
                if data:
                    if part["result"].get("base64Encoded", False):
                        chunks.append(base64.b64decode(data))
                    else:
                        chunks.append(data.encode())
                if part["result"].get("eof", False):
                    break
            self._send("IO.close", {"handle": stream_handle}, session_id=sid)
            pdf_path.write_bytes(b"".join(chunks))
            success = True
        elif "result" in printed and "data" in printed["result"]:
            pdf_path.write_bytes(base64.b64decode(printed["result"]["data"]))
            success = True

        self._send("Target.closeTarget", {"targetId": target_id})
        return success


def build_cover_html(title, subtitle="", doc_items=None):
    """A4 표지 HTML 생성."""
    today = date.today().strftime("%Y. %m")

    items_html = ""
    if doc_items:
        items_html = '<div class="toc-box">'
        items_html += '<div class="toc-title">목차</div>'
        for i, item in enumerate(doc_items, 1):
            items_html += (
                f'<div class="toc-entry">'
                f'<span class="toc-num">{i:02d}</span>'
                f'<span class="toc-text">{html_lib.escape(item)}</span>'
                f'</div>'
            )
        items_html += "</div>"

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <title>{html_lib.escape(title)}</title>
  <style>
    @page {{ size: A4; margin: 0; }}
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    html {{
      -webkit-print-color-adjust: exact !important;
      print-color-adjust: exact !important;
    }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans", Helvetica, Arial, sans-serif;
      background: #fff; color: #1f2328; margin: 0; padding: 0;
    }}
    .cover {{
      width: 210mm; height: 297mm; position: relative; display: flex;
      flex-direction: column; overflow: hidden;
    }}
    .top-bar {{
      height: 6px; background: #EF3B24; flex-shrink: 0;
    }}
    .logo-area {{
      padding: 48px 60px 0; flex-shrink: 0;
    }}
    .logo {{
      display: flex; align-items: baseline; gap: 8px;
    }}
    .logo-corporate {{
      font-size: 32px; font-weight: 900; color: #EF3B24;
      letter-spacing: -0.5px; line-height: 1;
    }}
    .logo-sub {{
      font-size: 18px; font-weight: 700; color: #6D6E71;
      letter-spacing: -0.3px;
    }}
    .cover-body {{
      flex: 1; display: flex; flex-direction: column;
      justify-content: center; padding: 0 60px;
    }}
    .accent-bar {{
      width: 48px; height: 4px; background: #EF3B24;
      margin-bottom: 28px;
    }}
    .title {{
      font-size: 40px; font-weight: 800; color: #1a1a1a;
      line-height: 1.25; margin-bottom: 16px;
      letter-spacing: -0.5px;
    }}
    .subtitle {{
      font-size: 17px; color: #6D6E71; font-weight: 400;
      line-height: 1.6; margin-bottom: 48px;
    }}
    .toc-box {{
      background: #f7f7f7; border-left: 4px solid #EF3B24;
      padding: 24px 28px; margin-bottom: 40px;
      max-width: 480px;
    }}
    .toc-title {{
      font-size: 12px; font-weight: 700; color: #6D6E71;
      text-transform: uppercase; letter-spacing: 3px;
      margin-bottom: 16px;
    }}
    .toc-entry {{
      display: flex; align-items: center; gap: 14px;
      padding: 7px 0; font-size: 15px; color: #333;
    }}
    .toc-num {{
      display: inline-flex; align-items: center; justify-content: center;
      width: 24px; height: 24px; border-radius: 50%;
      background: #EF3B24; color: #fff;
      font-size: 12px; font-weight: 700; flex-shrink: 0;
      font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, monospace;
    }}
    .toc-text {{ font-weight: 500; }}
    .footer {{
      padding: 28px 60px; flex-shrink: 0;
      display: flex; justify-content: space-between; align-items: flex-end;
    }}
    .footer-left {{
      display: flex; flex-direction: column; gap: 2px;
    }}
    .dept {{
      font-size: 14px; font-weight: 600; color: #6D6E71;
    }}
    .date {{
      font-size: 13px; color: #999;
    }}
    .footer-logo {{
      display: flex; align-items: baseline; gap: 5px;
    }}
    .footer-logo-corporate {{
      font-size: 18px; font-weight: 900; color: #EF3B24;
      letter-spacing: -0.3px;
    }}
    .footer-logo-sub {{
      font-size: 11px; font-weight: 700; color: #6D6E71;
    }}
    .side-accent {{
      position: absolute; left: 0; top: 0;
      width: 4px; height: 100%; background: transparent;
    }}
    .side-accent::before {{
      content: ""; position: absolute; left: 0; top: 54px;
      width: 4px; height: 60px; background: #EF3B24;
    }}
    .deco-corner {{
      position: absolute; bottom: 0; right: 0;
      width: 200px; height: 200px; overflow: hidden; pointer-events: none;
    }}
    .deco-corner::before {{
      content: ""; position: absolute; bottom: -60px; right: -60px;
      width: 200px; height: 200px; border-radius: 50%;
      border: 40px solid rgba(239, 59, 36, 0.05);
    }}
    .deco-corner::after {{
      content: ""; position: absolute; bottom: -30px; right: -30px;
      width: 120px; height: 120px; border-radius: 50%;
      border: 25px solid rgba(109, 110, 113, 0.04);
    }}
  </style>
</head>
<body>
  <div class="cover">
    <div class="top-bar"></div>
    <div class="logo-area">
      <div class="logo">
        <span class="logo-corporate">corporate</span>
        <span class="logo-sub">사내</span>
      </div>
    </div>
    <div class="side-accent"></div>
    <div class="cover-body">
      <div class="accent-bar"></div>
      <div class="title">{html_lib.escape(title)}</div>
      <div class="subtitle">{html_lib.escape(subtitle)}</div>
      {items_html}
    </div>
    <div class="footer">
      <div class="footer-left">
        <div class="dept">개발팀</div>
        <div class="date">{today}</div>
      </div>
      <div class="footer-logo">
        <span class="footer-logo-corporate">corporate</span>
        <span class="footer-logo-sub">사내</span>
      </div>
    </div>
    <div class="deco-corner"></div>
  </div>
</body>
</html>"""


def extract_sections(html_text, mode):
    if mode == "doc-contents":
        pattern = r'<div class="doc-content" id="docContent\d+"[^>]*>(.*?)\n</div>'
        sections = re.findall(pattern, html_text, re.DOTALL)
        if not sections:
            raise ValueError("doc-content 섹션을 찾을 수 없습니다. --mode를 확인하세요.")
        return sections

    if mode == "article":
        m = re.search(
            r'<article\s+class="markdown-body">\s*(.*?)\s*</article>',
            html_text,
            re.DOTALL,
        )
        if m:
            return [m.group(1)]
        m = re.search(r"<body>(.*?)</body>", html_text, re.DOTALL)
        return [m.group(1) if m else html_text]

    if mode == "body":
        m = re.search(r"<body>(.*?)</body>", html_text, re.DOTALL)
        if not m:
            raise ValueError("<body>를 찾을 수 없습니다. --mode를 확인하세요.")
        return [m.group(1)]

    if mode == "raw":
        return [html_text]

    raise ValueError(f"지원하지 않는 mode: {mode}")


def parse_cover_items(values):
    items = []
    for value in values or []:
        for part in value.split("|"):
            part = part.strip()
            if part:
                items.append(part)
    return items


def build_print_html(args, html_text):
    sections = extract_sections(html_text, args.mode)
    cover_style = ""
    body_parts = []

    if args.cover:
        cover_html = build_cover_html(
            title=args.title,
            subtitle=args.subtitle,
            doc_items=parse_cover_items(args.cover_item),
        )
        cover_body = re.search(r"<body>(.*?)</body>", cover_html, re.DOTALL).group(1)
        cover_style = re.search(r"<style>(.*?)</style>", cover_html, re.DOTALL).group(1)
        body_parts.append(f'<div class="cover-page">{cover_body}</div>')

    divider_class = "section-divider" if args.page_break_each_section else ""
    for section in sections:
        body_parts.append(
            f'<div class="{divider_class}"><div class="{args.content_class}">{section}</div></div>'
        )

    content_css = CONTENT_CSS_TEMPLATE.replace("__CONTENT_CLASS__", args.content_class)
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <title>{html_lib.escape(args.title)}</title>
  <style>{cover_style}
{content_css}
  </style>
</head>
<body>
{''.join(body_parts)}
</body>
</html>"""


def build_arg_parser():
    parser = argparse.ArgumentParser(description="HTML 파일을 PDF로 변환합니다.")
    parser.add_argument("--input", required=True, help="입력 HTML 파일 경로")
    parser.add_argument("--output", help="출력 PDF 파일 경로 (기본: 입력 파일명 기반)")
    parser.add_argument(
        "--mode",
        choices=["article", "body", "raw", "doc-contents"],
        default="article",
        help="본문 추출 방식 (기본: article)",
    )
    parser.add_argument("--title", help="문서 제목 (기본: 입력 파일명)")
    parser.add_argument("--subtitle", default="", help="표지 부제목")
    parser.add_argument("--cover", action="store_true", help="표지를 생성해 PDF 앞에 추가")
    parser.add_argument(
        "--cover-item",
        action="append",
        default=[],
        help="표지 목차 항목. 여러 번 사용 가능, 또는 'A|B|C' 형태 허용",
    )
    parser.add_argument(
        "--content-class",
        default="doc-content",
        help="본문 래퍼 CSS 클래스명 (기본: doc-content)",
    )
    parser.add_argument(
        "--page-break-each-section",
        action="store_true",
        help="섹션마다 페이지 나눔 적용",
    )
    parser.add_argument("--port", type=int, default=9222, help="Chrome CDP 포트 (기본: 9222)")
    parser.add_argument(
        "--chrome-path",
        default=DEFAULT_CHROME_PATH,
        help=f"Chrome 실행 경로 (기본: {DEFAULT_CHROME_PATH})",
    )
    parser.add_argument("--keep-temp", action="store_true", help="중간 HTML 파일 유지")
    return parser


def main():
    parser = build_arg_parser()
    args = parser.parse_args()

    input_path = Path(args.input).expanduser().resolve()
    if not input_path.exists():
        parser.error(f"입력 파일이 없습니다: {input_path}")

    output_path = (
        Path(args.output).expanduser().resolve()
        if args.output
        else input_path.with_suffix(".pdf")
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not args.title:
        args.title = input_path.stem

    html_text = input_path.read_text(encoding="utf-8")
    print_html = build_print_html(args, html_text)

    temp_path = output_path.parent / "_print_temp.html"
    temp_path.write_text(print_html, encoding="utf-8")
    print(f"  [HTML] 인쇄용 HTML 생성: {temp_path.name}")

    cdp = ChromeCDP(chrome_path=args.chrome_path, port=args.port)
    print("=== PDF 변환 ===")
    cdp.start()
    print("  [Chrome] CDP 연결 완료")
    try:
        if cdp.html_to_pdf(temp_path, output_path):
            print(f"  [PDF] 생성 완료: {output_path}")
        else:
            print("  [ERROR] PDF 생성 실패")
    finally:
        cdp.stop()
        if not args.keep_temp and temp_path.exists():
            temp_path.unlink()


if __name__ == "__main__":
    main()

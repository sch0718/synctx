import sys
import urllib.error
import urllib.request
from typing import Sequence
from urllib.parse import urlsplit

_ACCESS_HOST_SUFFIX = "cloudflareaccess.com"
_ACCESS_PATH_PREFIX = "/cdn-cgi/access/"
_DOCUMENT_MARKERS = ("markdown-body", "viewer-content", "page-static")
_HEAD_BYTES = 4096


def _is_access_location(location: str | None) -> bool:
    if not location:
        return False
    parts = urlsplit(location)
    host = (parts.hostname or "").lower()
    if host == _ACCESS_HOST_SUFFIX or host.endswith("." + _ACCESS_HOST_SUFFIX):
        return True
    return parts.path.startswith(_ACCESS_PATH_PREFIX)


def judge_access_response(status: int, location: str | None, body_head: str) -> bool:
    if any(marker in body_head for marker in _DOCUMENT_MARKERS):
        return False
    if status in (302, 303):
        return _is_access_location(location)
    return status in (401, 403)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_head(url: str, timeout: int = 20) -> tuple[int, str | None, str]:
    opener = urllib.request.build_opener(_NoRedirect)
    request = urllib.request.Request(url, headers={"User-Agent": "synctx-static-export-access-check"})
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, response.headers.get("Location"), response.read(_HEAD_BYTES).decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        with error:
            return error.code, error.headers.get("Location"), error.read(_HEAD_BYTES).decode("utf-8", "replace")


def main(argv: Sequence[str]) -> int:
    urls = list(argv)
    if not urls or any(urlsplit(u).scheme != "https" or not urlsplit(u).hostname for u in urls):
        print("사용법: check-access.sh https://<보호 호스트>/view/ [...] (https 주소만 받는다)")
        return 2
    failed = False
    for url in urls:
        try:
            status, location, body_head = fetch_head(url)
        except Exception as exc:
            print(f"실패 {url}: 요청 오류({type(exc).__name__}) — 막힘을 확인하지 못했다")
            failed = True
            continue
        if judge_access_response(status, location, body_head):
            print(f"통과 {url}: 인증 없는 요청이 막혔다(HTTP {status})")
        else:
            print(f"실패 {url}: 인증 없는 요청이 막히지 않았거나 확인할 수 없다(HTTP {status})")
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

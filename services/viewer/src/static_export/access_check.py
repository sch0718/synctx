import sys
import urllib.error
import urllib.request
from typing import Sequence
from urllib.parse import parse_qs, urljoin, urlsplit

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


def _is_login_location(url: str, location: str | None) -> bool:
    """로그인 모드(Pages Functions): 같은 출처의 `/login?next=…`인지. 상대 Location은 요청 URL 기준으로 푼다."""
    if not location:
        return False
    target, origin = urlsplit(urljoin(url, location)), urlsplit(url)
    same_origin = (target.scheme, (target.hostname or "").lower(), target.port) == (
        origin.scheme, (origin.hostname or "").lower(), origin.port)
    return same_origin and target.path == "/login" and "next" in parse_qs(target.query)


def judge_access_response(status: int, location: str | None, body_head: str, *, mode: str = "access",
                          url: str = "") -> bool:
    """인증 없는 요청이 막혔는지. access: Cloudflare Access 로그인·401·403, login: 같은 출처 `/login`으로 302."""
    if any(marker in body_head for marker in _DOCUMENT_MARKERS):
        return False
    if mode == "login":
        return status == 302 and _is_login_location(url, location)
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


_USAGE = "사용법: check-access.sh [--mode access|login] https://<보호 호스트>/view/ [...] (https 주소만 받는다)"


def main(argv: Sequence[str]) -> int:
    args, mode = list(argv), "access"
    if args[:1] == ["--mode"]:
        if len(args) < 2 or args[1] not in ("access", "login"):
            print(_USAGE)
            return 2
        mode, args = args[1], args[2:]
    urls = args
    if not urls or any(urlsplit(u).scheme != "https" or not urlsplit(u).hostname for u in urls):
        print(_USAGE)
        return 2
    failed = False
    for url in urls:
        try:
            status, location, body_head = fetch_head(url)
        except Exception as exc:
            print(f"실패 {url}: 요청 오류({type(exc).__name__}) — 막힘을 확인하지 못했다")
            failed = True
            continue
        if judge_access_response(status, location, body_head, mode=mode, url=url):
            print(f"통과 {url}: 인증 없는 요청이 막혔다(HTTP {status})")
        else:
            print(f"실패 {url}: 인증 없는 요청이 막히지 않았거나 확인할 수 없다(HTTP {status})")
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

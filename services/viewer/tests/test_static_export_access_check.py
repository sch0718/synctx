import subprocess
from pathlib import Path

import pytest

from static_export import access_check
from static_export.access_check import judge_access_response

ACCESS_LOGIN = "https://team.cloudflareaccess.com/cdn-cgi/access/login/docs.example.dev?kid=abc"


@pytest.mark.parametrize("status,location,body,expected", [
    (200, None, '<body class="page-static"><div class="markdown-body">본문</div>', False),
    (200, None, "", False),
    (302, ACCESS_LOGIN, "", True),
    (303, "https://docs.example.dev/cdn-cgi/access/login?redirect_url=%2F", "", True),
    (302, "https://team.cloudflareaccess.com/x", "", True),
    (302, "/view/docs/", "", False),
    (302, None, "", False),
    (301, ACCESS_LOGIN, "", False),
    (401, None, "", True),
    (403, None, "Access denied", True),
    (403, None, '<div class="viewer-content">본문</div>', False),
    (302, ACCESS_LOGIN, '<div class="markdown-body">본문</div>', False),
    (404, None, "", False),
    (500, None, "", False),
    (503, ACCESS_LOGIN, "", False),
])
def test_judge_access_response(status, location, body, expected):
    assert judge_access_response(status, location, body) is expected


SITE = "https://docs.example.dev/view/study-notes/"


@pytest.mark.parametrize("status,location,body,expected", [
    (302, "/login?next=%2Fview%2Fstudy-notes%2F", "", True),
    (302, "https://docs.example.dev/login?next=%2Fview%2F", "", True),
    (302, "https://evil.example/login?next=%2F", "", False),
    (302, "http://docs.example.dev/login?next=%2F", "", False),
    (302, "https://docs.example.dev:8443/login?next=%2F", "", False),
    (302, "/other?next=%2F", "", False),
    (302, "/login", "", False),
    (302, "//evil.example/login?next=%2F", "", False),
    (303, "/login?next=%2F", "", False),
    (302, "/login?next=%2F", '<div class="markdown-body">본문</div>', False),
    (200, None, '<body class="page-static">', False),
    (403, None, "", False),
    (503, None, "", False),
    (302, ACCESS_LOGIN, "", False),
])
def test_judge_login_mode(status, location, body, expected):
    assert judge_access_response(status, location, body, mode="login", url=SITE) is expected


def test_main_passes_mode_to_judge(monkeypatch):
    monkeypatch.setattr(access_check, "fetch_head", lambda url, timeout=20: (302, "/login?next=%2F", ""))
    assert access_check.main(["--mode", "login", SITE]) == 0
    assert access_check.main([SITE]) == 1
    assert access_check.main(["--mode", "other", SITE]) == 2
    assert access_check.main(["--mode", "login"]) == 2


def test_judge_does_not_trust_lookalike_host():
    assert judge_access_response(302, "https://evilcloudflareaccess.com.example.net/x", "") is False


def test_main_without_urls_fails(capsys):
    assert access_check.main([]) == 2


def test_main_fails_when_any_url_not_blocked(monkeypatch, capsys):
    results = {"https://a.example/": (302, ACCESS_LOGIN, ""), "https://b.example/": (200, None, "<div class=\"markdown-body\">")}
    monkeypatch.setattr(access_check, "fetch_head", lambda url, timeout=20: results[url])
    assert access_check.main(["https://a.example/", "https://b.example/"]) == 1
    out = capsys.readouterr().out
    assert "https://a.example/" in out and "https://b.example/" in out


def test_main_passes_when_all_blocked(monkeypatch):
    monkeypatch.setattr(access_check, "fetch_head", lambda url, timeout=20: (403, None, ""))
    assert access_check.main(["https://a.example/"]) == 0


def test_main_treats_network_error_as_failure(monkeypatch, capsys):
    def boom(url, timeout=20):
        raise OSError("secret-detail")
    monkeypatch.setattr(access_check, "fetch_head", boom)
    assert access_check.main(["https://a.example/"]) == 1
    assert "secret-detail" not in capsys.readouterr().out


def test_main_rejects_non_https_url(monkeypatch):
    monkeypatch.setattr(access_check, "fetch_head", lambda url, timeout=20: (403, None, ""))
    assert access_check.main(["file:///etc/passwd"]) == 2


def test_check_access_script_exists_and_fails_without_args():
    script = Path(__file__).resolve().parents[3] / "scripts" / "static-export" / "check-access.sh"
    assert script.is_file()
    result = subprocess.run(["bash", str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    assert "사용법" in result.stdout + result.stderr

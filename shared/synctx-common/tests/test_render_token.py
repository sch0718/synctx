import base64
import hashlib
import hmac
import subprocess
import sys

import pytest

from synctx_common import render_token
from synctx_common.render_token import (
    RenderTokenError,
    issue_render_token,
    load_render_keys,
    verify_render_token,
)

KEY = b"current-render-key-for-tests-12345"
PREVIOUS_KEY = b"previous-render-key-for-tests-1234"
SHA = "0123456789abcdef" * 2 + "01234567"
NOW = 1700000000


def _b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _signed_token(repo_id="r", ref="main", sha=SHA, exp=str(NOW + 60), key=KEY):
    ref_bytes = ref.encode("utf-8") if isinstance(ref, str) else ref
    message = b"render|v1|" + repo_id.encode("utf-8") + b"|" + ref_bytes + f"|{sha}|{exp}".encode("utf-8")
    signature = hmac.new(key, message, hashlib.sha256).digest()[:16]
    return f"v1.{exp}.{_b64(ref_bytes)}.{sha}.{_b64(signature)}"


def test_issue_and_verify_use_the_documented_wire_format():
    token = issue_render_token("r", "feature/한글", SHA, key=KEY, ttl_seconds=60, now=NOW)
    assert token == _signed_token(ref="feature/한글")
    version, exp, ref, sha, signature = token.split(".")
    assert (version, exp, ref, sha) == ("v1", str(NOW + 60), _b64("feature/한글".encode()), SHA)
    assert len(signature) == 22
    assert "=" not in token and "/" not in token and "+" not in token
    decoded_signature = base64.urlsafe_b64decode(signature + "==")
    assert len(decoded_signature) == 16
    assert decoded_signature == hmac.new(
        KEY, f"render|v1|r|feature/한글|{SHA}|{NOW + 60}".encode(), hashlib.sha256,
    ).digest()[:16]
    assert verify_render_token(token, "r", key=KEY, now=NOW) == ("feature/한글", SHA)


def test_default_ttl_and_clock(monkeypatch):
    monkeypatch.setattr(render_token.time, "time", lambda: NOW + 0.9)
    token = issue_render_token("r", "main", SHA, key=KEY)
    assert token == _signed_token(exp=str(NOW + 14400))
    assert verify_render_token(token, "r", key=KEY) == ("main", SHA)


@pytest.mark.parametrize("field,value", [(1, str(NOW + 61)), (2, _b64(b"other")), (3, "f" * 40), (4, _b64(b"x" * 16))])
def test_tampering_is_rejected(field, value):
    parts = _signed_token().split(".")
    parts[field] = value
    with pytest.raises(RenderTokenError):
        verify_render_token(".".join(parts), "r", key=KEY, now=NOW)


@pytest.mark.parametrize(
    "token",
    [
        "", "v1", _signed_token().replace("v1.", "v2.", 1),
        _signed_token() + ".extra", _signed_token().rsplit(".", 1)[0],
        _signed_token() + "\n", _signed_token() + "\r\n", " " + _signed_token(),
        _signed_token(ref=b"abcde").replace(_b64(b"abcde"), "abcde", 1),
    ],
)
def test_invalid_version_or_format_is_rejected(token):
    with pytest.raises(RenderTokenError):
        verify_render_token(token, "r", key=KEY, now=NOW)


def test_token_is_bound_to_repo_id():
    with pytest.raises(RenderTokenError):
        verify_render_token(_signed_token(), "other", key=KEY, now=NOW)
    token = issue_render_token("저장소", "main", SHA, key=KEY, now=NOW)
    assert verify_render_token(token, "저장소", key=KEY, now=NOW) == ("main", SHA)


@pytest.mark.parametrize("repo_id", ["r|other", "r\ud800"])
def test_invalid_repo_id_is_rejected(repo_id):
    with pytest.raises(RenderTokenError):
        issue_render_token(repo_id, "main", SHA, key=KEY, now=NOW)
    with pytest.raises(RenderTokenError):
        verify_render_token(_signed_token(), repo_id, key=KEY, now=NOW)


@pytest.mark.parametrize("now,expired", [(NOW + 59, False), (NOW + 60, True), (NOW + 61, True)])
def test_expiration_boundary(now, expired):
    if expired:
        with pytest.raises(RenderTokenError):
            verify_render_token(_signed_token(), "r", key=KEY, now=now)
    else:
        assert verify_render_token(_signed_token(), "r", key=KEY, now=now) == ("main", SHA)


@pytest.mark.parametrize("sha", ["a" * 39, "a" * 41, "a" * 64, "A" * 40, "g" * 40, "a" * 40 + "\n"])
def test_only_lowercase_40_character_sha_is_supported(sha):
    with pytest.raises(RenderTokenError):
        issue_render_token("r", "main", sha, key=KEY, now=NOW)
    with pytest.raises(RenderTokenError):
        verify_render_token(_signed_token(sha=sha), "r", key=KEY, now=NOW)


@pytest.mark.parametrize("ref", ["a", "@", "feature/a/b", "한글/브랜치", "a" * 255, "가" * 85, "가" * 84 + "ab"])
def test_valid_branches_and_utf8_byte_boundaries(ref):
    token = issue_render_token("r", ref, SHA, key=KEY, now=NOW)
    assert verify_render_token(token, "r", key=KEY, now=NOW) == (ref, SHA)


@pytest.mark.parametrize(
    "ref",
    [
        "", "a" * 256, "가" * 85 + "a", "가" * 86,
        "HEAD", "-main", ".main", "a/.b", "a.lock", "a.lock/b", "a..b", "a@{b",
        "a b", "a~b", "a^b", "a:b", "a?b", "a*b", "a[b", "a\\b", "a|b",
        "a//b", "/main", "main/", "main.", "a\x00b", "a\nb", "a\rb", "a\tb", "a\x7fb", "a\x85b",
    ],
)
def test_invalid_branches_are_rejected_on_issue_and_verify(ref):
    with pytest.raises(RenderTokenError):
        issue_render_token("r", ref, SHA, key=KEY, now=NOW)
    with pytest.raises(RenderTokenError):
        verify_render_token(_signed_token(ref=ref), "r", key=KEY, now=NOW)


def test_invalid_utf8_ref_is_rejected():
    with pytest.raises(RenderTokenError):
        issue_render_token("r", "a\ud800", SHA, key=KEY, now=NOW)
    with pytest.raises(RenderTokenError):
        verify_render_token(_signed_token(ref=b"\xff"), "r", key=KEY, now=NOW)


def test_maximum_token_length_and_expiration_text_signature():
    token = issue_render_token("r", "a" * 255, SHA, key=KEY, now=99999999998, ttl_seconds=1)
    assert len(token) == 419
    assert token == _signed_token(ref="a" * 255, exp="99999999999")
    assert verify_render_token(token, "r", key=KEY, now=99999999998) == ("a" * 255, SHA)


@pytest.mark.parametrize("exp", ["0", "00", "01700000060", "١٧٠٠٠٠٠٠٦٠", "１７００００００６０", "+1700000060", "1700000060.0", "100000000000"])
def test_noncanonical_expiration_is_rejected_even_with_matching_signature(exp):
    token = _signed_token(exp=exp)
    with pytest.raises(RenderTokenError):
        verify_render_token(token, "r", key=KEY, now=NOW)
    parts = token.split(".")
    parts[-1] = _signed_token().split(".")[-1]
    with pytest.raises(RenderTokenError):
        verify_render_token(".".join(parts), "r", key=KEY, now=NOW)


@pytest.mark.parametrize("field", [2, 4])
@pytest.mark.parametrize("padding", ["=", "=="])
def test_base64url_padding_is_rejected(field, padding):
    parts = _signed_token().split(".")
    parts[field] += padding
    with pytest.raises(RenderTokenError):
        verify_render_token(".".join(parts), "r", key=KEY, now=NOW)


@pytest.mark.parametrize("field", [2, 4])
def test_base64url_alias_encodings_are_rejected(field):
    parts = _signed_token(ref="a").split(".")
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    original = parts[field]
    parts[field] = original[:-1] + alphabet[alphabet.index(original[-1]) + 1]
    assert base64.urlsafe_b64decode(parts[field] + "==") == base64.urlsafe_b64decode(original + "==")
    with pytest.raises(RenderTokenError):
        verify_render_token(".".join(parts), "r", key=KEY, now=NOW)


@pytest.mark.parametrize("length", [0, 15, 17, 32])
def test_signature_must_be_exactly_16_bytes(length):
    parts = _signed_token().split(".")
    parts[-1] = _b64(b"x" * length)
    with pytest.raises(RenderTokenError):
        verify_render_token(".".join(parts), "r", key=KEY, now=NOW)


def test_signature_uses_urlsafe_alphabet():
    token = _signed_token(ref="a", exp="1700000001")
    signature = token.split(".")[-1]
    assert "+" not in signature and "/" not in signature and "=" not in signature
    assert verify_render_token(token, "r", key=KEY, now=NOW) == ("a", SHA)


def test_key_rotation_issues_with_current_and_verifies_previous(monkeypatch):
    key, previous = load_render_keys({
        "SYNCTX_RENDER_SECRET": KEY.decode(), "SYNCTX_RENDER_SECRET_PREVIOUS": PREVIOUS_KEY.decode(),
    })
    token = issue_render_token("r", "main", SHA, key=key, ttl_seconds=60, now=NOW)
    assert token == _signed_token(key=KEY)
    assert token != _signed_token(key=PREVIOUS_KEY)
    original = hmac.compare_digest
    calls = []

    def compare(expected, signature):
        calls.append(expected)
        return original(expected, signature)

    monkeypatch.setattr(render_token.hmac, "compare_digest", compare)
    assert verify_render_token(token, "r", key=key, previous_key=previous, now=NOW) == ("main", SHA)
    assert len(calls) == 1
    calls.clear()
    old_token = _signed_token(key=PREVIOUS_KEY)
    assert verify_render_token(old_token, "r", key=key, previous_key=previous, now=NOW) == ("main", SHA)
    assert calls == [base64.urlsafe_b64decode(t.split(".")[-1] + "==") for t in (token, old_token)]
    with pytest.raises(RenderTokenError):
        verify_render_token(old_token, "r", key=key, now=NOW)
    with pytest.raises(RenderTokenError):
        verify_render_token(token, "r", key=previous, now=NOW)


@pytest.mark.parametrize("ttl", [0, -1, 1.5, True])
def test_invalid_ttl_is_rejected(ttl):
    with pytest.raises(RenderTokenError):
        issue_render_token("r", "main", SHA, key=KEY, ttl_seconds=ttl, now=NOW)


@pytest.mark.parametrize("now,ttl", [(-2, 1), (99999999999, 1), (NOW + 0.5, 1)])
def test_issue_rejects_unrepresentable_expiration(now, ttl):
    with pytest.raises(RenderTokenError):
        issue_render_token("r", "main", SHA, key=KEY, ttl_seconds=ttl, now=now)


def test_key_loading_from_environment_and_utf8_bytes(monkeypatch):
    monkeypatch.setenv("SYNCTX_RENDER_SECRET", "가" * 11)
    monkeypatch.setenv("SYNCTX_RENDER_SECRET_PREVIOUS", PREVIOUS_KEY.decode())
    monkeypatch.delenv("SYNCTX_RENDER_SECRET_FILE", raising=False)
    monkeypatch.delenv("SYNCTX_RENDER_SECRET_PREVIOUS_FILE", raising=False)
    assert load_render_keys() == (("가" * 11).encode(), PREVIOUS_KEY)
    assert load_render_keys({"SYNCTX_RENDER_SECRET": KEY.decode()}) == (KEY, None)


@pytest.mark.parametrize("name", ["SYNCTX_RENDER_SECRET", "SYNCTX_RENDER_SECRET_PREVIOUS"])
@pytest.mark.parametrize("value", ["", "x" * 31, "가" * 10, "\ud800" * 32])
def test_short_or_invalid_utf8_keys_are_startup_errors(name, value):
    with pytest.raises(RenderTokenError):
        load_render_keys({"SYNCTX_RENDER_SECRET": KEY.decode(), name: value})


@pytest.mark.parametrize("name", ["SYNCTX_RENDER_SECRET", "SYNCTX_RENDER_SECRET_PREVIOUS"])
@pytest.mark.parametrize("direct", ["", KEY.decode()])
def test_direct_and_file_settings_conflict(tmp_path, name, direct):
    path = tmp_path / "key"
    path.write_bytes(KEY)
    with pytest.raises(RenderTokenError):
        load_render_keys({"SYNCTX_RENDER_SECRET": KEY.decode(), name: direct, name + "_FILE": str(path)})


@pytest.mark.parametrize("name", ["SYNCTX_RENDER_SECRET", "SYNCTX_RENDER_SECRET_PREVIOUS"])
@pytest.mark.parametrize("kind", ["missing", "directory", "invalid_utf8", "short", "empty_path"])
def test_invalid_key_files_are_startup_errors(tmp_path, name, kind):
    path = tmp_path / "key"
    if kind == "directory":
        path.mkdir()
    elif kind == "invalid_utf8":
        path.write_bytes(b"\xff" * 32)
    elif kind == "short":
        path.write_bytes(b"x" * 31 + b"\r\n")
    env = {name + "_FILE": "" if kind == "empty_path" else str(path)}
    if name != "SYNCTX_RENDER_SECRET":
        env["SYNCTX_RENDER_SECRET"] = KEY.decode()
    with pytest.raises(RenderTokenError):
        load_render_keys(env)


@pytest.mark.parametrize("file_setting", [False, True])
def test_previous_key_without_current_is_a_startup_error(tmp_path, file_setting):
    path = tmp_path / "previous"
    path.write_bytes(PREVIOUS_KEY)
    env = {"SYNCTX_RENDER_SECRET_PREVIOUS_FILE": str(path)} if file_setting else {"SYNCTX_RENDER_SECRET_PREVIOUS": PREVIOUS_KEY.decode()}
    with pytest.raises(RenderTokenError):
        load_render_keys(env)


@pytest.mark.parametrize("suffix,remaining", [(b"", b""), (b"\n", b""), (b"\r\n", b""), (b"\n\n", b"\n"), (b"\r\n\r\n", b"\r\n"), (b"\r", b"\r")])
def test_key_files_strip_only_one_final_lf_or_crlf(tmp_path, suffix, remaining):
    current = tmp_path / "current"
    previous = tmp_path / "previous"
    current.write_bytes(KEY + suffix)
    previous.write_bytes(PREVIOUS_KEY + suffix)
    assert load_render_keys({
        "SYNCTX_RENDER_SECRET_FILE": str(current), "SYNCTX_RENDER_SECRET_PREVIOUS_FILE": str(previous),
    }) == (KEY + remaining, PREVIOUS_KEY + remaining)


def test_direct_keys_are_utf8_text_without_base64_decoding_or_newline_trimming():
    value = _b64(b"x" * 32) + "\n"
    assert load_render_keys({"SYNCTX_RENDER_SECRET": value}) == (value.encode(), None)


def test_random_key_is_generated_once_and_reused(monkeypatch):
    monkeypatch.setattr(render_token, "_RANDOM_KEY", None)
    calls = []

    def random_bytes(length):
        calls.append(length)
        return b"x" * length

    monkeypatch.setattr(render_token.secrets, "token_bytes", random_bytes)
    assert load_render_keys({}) == (b"x" * 32, None)
    assert load_render_keys({"SYNCTX_RENDER_SECRET": KEY.decode()}) == (KEY, None)
    assert load_render_keys({}) == (b"x" * 32, None)
    assert calls == [32]


def test_process_restart_cannot_verify_a_random_key_token():
    key, previous = load_render_keys({})
    assert len(key) >= 32 and previous is None
    token = issue_render_token("r", "main", SHA, key=key, now=NOW)
    assert verify_render_token(token, "r", key=load_render_keys({})[0], now=NOW) == ("main", SHA)
    script = """
import sys
from synctx_common.render_token import RenderTokenError, load_render_keys, verify_render_token
key, _ = load_render_keys({})
assert key == load_render_keys({})[0]
try:
    verify_render_token(sys.argv[1], 'r', key=key, now=int(sys.argv[2]))
except RenderTokenError:
    pass
else:
    raise AssertionError('a new process must not reuse the old random key')
"""
    subprocess.run([sys.executable, "-c", script, token, str(NOW)], check=True, capture_output=True)

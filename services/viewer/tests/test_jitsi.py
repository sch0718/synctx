import base64
import hashlib
import hmac
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

import jitsi
from synctx_common.config import JitsiConfig
from synctx_common.secret_keys import SecretKeyError
from test_meeting_index import entry

NOW = datetime(2026, 10, 28, 3, tzinfo=timezone.utc)
APP = b"app-key-for-jitsi-testing-only-123456"
ROOM = b"room-key-for-jitsi-testing-only-12345"
OLD = b"previous-key-for-testing-only-1234567"
CFG = JitsiConfig(enabled=True, public_url="https://meet.synctx.local:8443", app_id="synctx")
ENV = {CFG.secret_env: APP.decode(), CFG.room_secret_env: ROOM.decode()}


def meeting(**kw):
    return entry(starts_at=NOW, **kw)


def decode(token):
    return json.loads(base64.urlsafe_b64decode(token.split('.')[1] + '=='))


def test_deterministic_separate_rooms():
    expected = hmac.new(ROOM, b"r|m1", hashlib.sha256).hexdigest()[:32]
    assert jitsi.room_name(ROOM, "r", "m1") == expected
    assert jitsi.room_name(ROOM, "r", "m1") != jitsi.room_name(ROOM, "r2", "m1")
    assert jitsi.room_name(ROOM, "r", "m1") != jitsi.room_name(OLD, "r", "m1")
    with pytest.raises(ValueError):
        jitsi.room_name(ROOM, "r|bad", "m1")


def test_signature_claims_and_expiry():
    keys = jitsi.load_keys(CFG, ENV)
    m = meeting(presenters=["Presenter"])
    data = jitsi.issue_token(CFG, keys, "r", m, "presenter", presenter_identity="presenter", authz_enabled=True, now=NOW)
    unsigned, signature = data['jwt'].rsplit('.', 1)
    expected = base64.urlsafe_b64encode(hmac.new(APP, unsigned.encode(), hashlib.sha256).digest()).rstrip(b'=').decode()
    assert signature == expected
    assert json.loads(base64.urlsafe_b64decode(data['jwt'].split('.')[0] + '==')) == {"alg": "HS256", "typ": "JWT"}
    claims = decode(data['jwt'])
    assert claims['iss'] == claims['aud'] == "synctx"
    assert claims['sub'] == "meet.synctx.local"
    assert claims['room'] == data['room']
    assert claims['iat'] == int(NOW.timestamp()) and claims['nbf'] == int(NOW.timestamp()) - 10
    assert claims['exp'] == int((NOW + timedelta(minutes=m.duration_minutes, hours=1)).timestamp())
    assert claims['context']['user'] == {'name': 'presenter', 'moderator': True}
    assert data['moderator'] is True and data['domain'] == "meet.synctx.local"
    assert datetime.fromisoformat(data['expires_at']).timestamp() == claims['exp']
    long = jitsi.issue_token(CFG, keys, "r", meeting(duration_minutes=600), now=NOW)
    assert decode(long['jwt'])['exp'] == int((NOW + timedelta(hours=4)).timestamp())


def test_rotation_signs_current_only_and_room_key_is_independent():
    keys = jitsi.load_keys(CFG, {**ENV, CFG.secret_env + '_PREVIOUS': OLD.decode(), CFG.room_secret_env + '_PREVIOUS': OLD.decode()})
    assert keys.previous == keys.room_previous == OLD
    old = jitsi.issue_token(CFG, keys, "r", meeting(), now=NOW)
    new = jitsi.issue_token(CFG, replace(keys, current=OLD, previous=APP), "r", meeting(), now=NOW)
    assert old['room'] == new['room'] and old['jwt'] != new['jwt']
    assert decode(old['jwt']) == decode(new['jwt'])


def test_token_subject_uses_xmpp_domain_and_domain_stays_public_host():
    cfg = CFG.model_copy(update={'xmpp_domain': 'xmpp.synctx.local'})
    data = jitsi.issue_token(cfg, jitsi.load_keys(cfg, ENV), "r", meeting(), now=NOW)
    assert decode(data['jwt'])['sub'] == 'xmpp.synctx.local'
    assert data['domain'] == 'meet.synctx.local'


def test_token_defaults_to_attendee_without_authorization():
    data = jitsi.issue_token(CFG, jitsi.load_keys(CFG, ENV), "r", meeting(presenters=['Alice']), 'Alice',
                             presenter_identity='Alice', now=NOW)
    assert decode(data['jwt'])['context']['user'] == {'name': 'Alice', 'moderator': False}


@pytest.mark.parametrize('delta,reason', [(-901, 'too_early'), (-900, None), (0, None), (7199, None), (7200, 'ended')])
def test_join_window(delta, reason):
    assert jitsi.availability(CFG, meeting(duration_minutes=60), NOW + timedelta(seconds=delta)) == reason


def test_cancelled_and_disabled():
    keys = jitsi.load_keys(CFG, ENV)
    for cfg, m, reason in [(CFG, meeting(cancelled=True), 'cancelled'), (JitsiConfig(), meeting(), 'disabled')]:
        with pytest.raises(jitsi.JitsiError) as exc:
            jitsi.issue_token(cfg, keys, "r", m, now=NOW)
        assert exc.value.reason == reason
    assert jitsi.load_keys(JitsiConfig(), {}) is None


@pytest.mark.parametrize('identity,moderator', [('', False), (' Alice ', True), ('ALICE', True), ('other', False), ('Ali\x00ce', False)])
def test_identity(identity, moderator):
    name, actual = jitsi.participant(identity, ['alice'], presenter_identity=identity, authz_enabled=True)
    assert actual is moderator
    assert '\x00' not in name
    if not identity:
        assert name == '참석자'
    assert len(jitsi.participant('a' * 200 + '\u202e', [])[0]) == 80


def test_display_name_is_sanitized_independently_of_presenter_identity():
    name, moderator = jitsi.participant(' Bob\x00\u202e ', ['alice@example.com'],
                                        presenter_identity=' ALICE@example.com ', authz_enabled=True)
    assert name == 'Bob' and moderator is True
    name, moderator = jitsi.participant('Alice', ['Alice'], authz_enabled=True)
    assert name == 'Alice' and moderator is False


@pytest.mark.parametrize('name', ['JITSI_APP_SECRET', 'JITSI_APP_SECRET_PREVIOUS', 'JITSI_ROOM_SECRET', 'JITSI_ROOM_SECRET_PREVIOUS'])
@pytest.mark.parametrize('value', ['', 'short', '가' * 10])
def test_rejects_short_current_and_previous_keys(name, value):
    with pytest.raises(SecretKeyError):
        jitsi.load_keys(CFG, {**ENV, name: value})


@pytest.mark.parametrize('name', ['JITSI_APP_SECRET', 'JITSI_ROOM_SECRET'])
def test_requires_current_even_with_previous(name):
    env = {**ENV, name + '_PREVIOUS': OLD.decode()}
    del env[name]
    with pytest.raises(SecretKeyError, match='현재 키'):
        jitsi.load_keys(CFG, env)


@pytest.mark.parametrize('name', ['JITSI_APP_SECRET', 'JITSI_APP_SECRET_PREVIOUS', 'JITSI_ROOM_SECRET', 'JITSI_ROOM_SECRET_PREVIOUS'])
def test_file_keys_and_conflicts(tmp_path, name):
    path = tmp_path / 'key'
    path.write_bytes(OLD + b'\r\n')
    env = {**ENV, name + '_FILE': str(path)}
    env.pop(name, None)
    keys = jitsi.load_keys(CFG, env)
    slot = {'JITSI_APP_SECRET': 'current', 'JITSI_APP_SECRET_PREVIOUS': 'previous',
            'JITSI_ROOM_SECRET': 'room_current', 'JITSI_ROOM_SECRET_PREVIOUS': 'room_previous'}[name]
    assert getattr(keys, slot) == OLD
    with pytest.raises(SecretKeyError):
        jitsi.load_keys(CFG, {**env, name: APP.decode()})
    path.write_bytes(b'short\n')
    with pytest.raises(SecretKeyError):
        jitsi.load_keys(CFG, env)
    path.unlink()
    with pytest.raises(SecretKeyError):
        jitsi.load_keys(CFG, env)

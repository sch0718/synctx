import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import meeting_index
from synctx_common.config import AuthzConfig, AuthzRoleMappings, CollabConfig, SynctxConfig, ViewerConfig
from synctx_common.secret_keys import SecretKeyError
from test_jitsi import CFG, ENV, decode
from test_meeting_index import FakeManager, meeting_doc
from test_meetings_dashboard import _profile


@pytest.fixture
def client(monkeypatch):
    import app
    now = datetime.now(timezone.utc)
    cfg = SynctxConfig(authz=AuthzConfig(enabled=True, role_mappings=AuthzRoleMappings(viewer=['synctx-viewer'])),
                       collab=CollabConfig(jitsi=CFG),
                       viewer=ViewerConfig(asset_source='bundled'), repositories=[_profile('r'), _profile('private', False)])
    monkeypatch.setattr(app, 'config', cfg)
    monkeypatch.setattr(app, 'jitsi_keys', app.jitsi.load_keys(CFG, ENV))
    manager = FakeManager({
        'now.md': meeting_doc(mid='now', starts=now.isoformat(), extra='    presenters: [Alice]\n'),
        'cancelled.md': meeting_doc(mid='cancelled', starts=now.isoformat(), extra='    cancelled: true\n'),
        'future.md': meeting_doc(mid='future', starts=(now + timedelta(days=1)).isoformat()),
        'past.md': meeting_doc(mid='past', starts=(now - timedelta(days=1)).isoformat()),
        'invalid.md': meeting_doc(mid='invalid', starts='invalid'),
    })
    manager.profile = cfg.repositories[0]
    monkeypatch.setattr(app, 'repo_managers', {'r': manager})
    meeting_index.clear_cache()
    return TestClient(app.app, raise_server_exceptions=False)


GROUPS = {'X-Forwarded-Groups': 'synctx-viewer'}


def test_api_access_no_store_and_metrics(client, monkeypatch):
    import app
    monkeypatch.setenv(app.config.collab.gateway.viewer_service_token_env, 'service-token')
    metric = app.viewer_jitsi_token_failures_total.labels(reason='forbidden')
    before = metric._value.get()
    for headers in ({}, {'Authorization': 'Bearer service-token'}, {'X-Forwarded-Groups': 'cdn-admin'}):
        response = client.get('/api/meetings/r/now/jitsi', headers=headers)
        assert response.status_code == 403
        assert response.headers['cache-control'] == 'no-store'
        assert 'jwt' not in response.text
    assert metric._value.get() == before + 3
    assert client.get('/api/meetings', headers={'Authorization': 'Bearer service-token'}).status_code == 200
    before = app.viewer_jitsi_tokens_issued_total._value.get()
    response = client.get('/api/meetings/r/now/jitsi', headers={**GROUPS, 'X-Forwarded-User': 'aLiCe'})
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    assert set(response.json()) == {'room', 'jwt', 'domain', 'public_url', 'moderator', 'expires_at'}
    assert response.json()['moderator'] is True
    assert app.viewer_jitsi_tokens_issued_total._value.get() == before + 1
    assert 'synctx_viewer_jitsi_tokens_issued_total' in client.get('/metrics').text


@pytest.mark.parametrize('identity', ['', 'Alice'])
def test_local_user_is_attendee_even_with_presenter_header(client, monkeypatch, identity):
    import app
    monkeypatch.setattr(app.config.authz, 'enabled', False)
    response = client.get('/api/meetings/r/now/jitsi', headers={
        'X-Forwarded-User': identity, 'X-Forwarded-Email': 'Alice', 'X-Forwarded-Preferred-Username': 'Alice'})
    assert response.status_code == 200 and response.json()['moderator'] is False
    assert decode(response.json()['jwt'])['context']['user'] == {'name': identity or '참석자', 'moderator': False}


@pytest.mark.parametrize('header', ['X-Forwarded-User', 'X-Forwarded-Email', 'X-Forwarded-Preferred-Username'])
def test_presenter_comparison_uses_only_selected_header(client, monkeypatch, header):
    import app
    monkeypatch.setattr(app.config.collab.jitsi, 'presenter_header', header)
    for match in (False, True):
        headers = {**GROUPS, 'X-Forwarded-User': 'Alice', 'X-Forwarded-Email': 'Alice',
                   'X-Forwarded-Preferred-Username': 'Alice', header: ' aLiCe ' if match else 'other'}
        response = client.get('/api/meetings/r/now/jitsi', headers=headers)
        assert response.status_code == 200
        user = decode(response.json()['jwt'])['context']['user']
        assert user == {'name': headers['X-Forwarded-User'].strip(), 'moderator': match}
        assert response.json()['moderator'] is match


@pytest.mark.parametrize('repo,mid', [('private', 'now'), ('missing', 'now'), ('r', 'missing'), ('r', 'invalid')])
def test_missing_and_unvalidated_meetings(client, repo, mid):
    response = client.get(f'/api/meetings/{repo}/{mid}/jitsi', headers=GROUPS)
    assert response.status_code == 404 and response.headers['cache-control'] == 'no-store'


@pytest.mark.parametrize('mid,reason', [('future', 'too_early'), ('past', 'ended'), ('cancelled', 'cancelled')])
def test_window_rejections(client, mid, reason):
    import app
    metric = app.viewer_jitsi_token_failures_total.labels(reason=reason)
    before = metric._value.get()
    response = client.get(f'/api/meetings/r/{mid}/jitsi', headers=GROUPS)
    assert response.status_code == 409 and response.json()['detail']['reason'] == reason
    assert response.headers['cache-control'] == 'no-store'
    assert metric._value.get() == before + 1


def test_disabled_and_public_metadata(client, monkeypatch):
    import app
    monkeypatch.setattr(app.config.collab.jitsi, 'enabled', False)
    response = client.get('/api/meetings/r/now/jitsi', headers=GROUPS)
    assert response.status_code == 409 and response.json()['detail']['reason'] == 'disabled'
    data = client.get('/api/meetings', headers=GROUPS).json()
    meta = next(m for m in data['meetings'] if m['id'] == 'now')['jitsi']
    assert meta['enabled'] is False
    assert set(meta) == {'enabled', 'joinable_from', 'joinable_until'}
    assert all(secret not in str(data) for secret in ENV.values())


def test_enabled_csp_limits_jitsi_to_script_and_frame(client, monkeypatch):
    import app
    monkeypatch.setattr(app.config.authz, 'enabled', False)
    response = client.get('/')
    directives = dict(part.strip().split(' ', 1) for part in response.headers['content-security-policy'].split('; '))
    origin = CFG.public_url
    assert directives['script-src'] == "'self' 'unsafe-inline' " + origin
    assert directives['frame-src'] == origin
    assert directives['connect-src'] == "'self'"
    assert response.headers['permissions-policy'] == ', '.join(f'{f}=(self "{origin}")' for f in ('camera', 'microphone', 'display-capture'))


def test_disabled_headers_match_original_bytes(client, monkeypatch):
    import app
    monkeypatch.setattr(app.config.authz, 'enabled', False)
    monkeypatch.setattr(app.config.collab.jitsi, 'enabled', False)
    response = client.get('/')
    assert response.headers['content-security-policy'] == (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; font-src 'self' data:; connect-src 'self'; frame-src 'none'; object-src 'none'"
    )
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert response.headers['x-frame-options'] == 'DENY'
    assert 'permissions-policy' not in response.headers


def test_missing_current_key_fails_lifespan_before_repo_start(client, monkeypatch):
    import app
    for name in (CFG.secret_env, CFG.room_secret_env):
        for suffix in ('', '_FILE', '_PREVIOUS', '_PREVIOUS_FILE'):
            monkeypatch.delenv(name + suffix, raising=False)

    async def startup():
        async with app.lifespan(app.app):
            pytest.fail('startup must fail')

    with pytest.raises(SecretKeyError, match='현재 키'):
        asyncio.run(startup())


def test_duplicate_id_is_not_issued(client):
    import app
    manager = app.repo_managers['r']
    manager.files['duplicate.md'] = manager.files['now.md']
    meeting_index.clear_cache()
    assert client.get('/api/meetings/r/now/jitsi', headers=GROUPS).status_code == 404


def test_read_failure_is_counted_and_has_fixed_detail(client, monkeypatch):
    import app

    def failed(repo_id):
        raise ValueError('private-exception-detail')

    monkeypatch.setattr(app, '_collect_meetings', failed)
    metric = app.viewer_jitsi_token_failures_total.labels(reason='unavailable')
    before = metric._value.get()
    response = client.get('/api/meetings/r/now/jitsi', headers=GROUPS)
    assert response.status_code == 503 and response.json()['detail']['reason'] == 'unavailable'
    assert response.headers['cache-control'] == 'no-store'
    assert 'private-exception-detail' not in response.text and metric._value.get() == before + 1


def test_ref_parameter_cannot_select_another_branch(client):
    response = client.get('/api/meetings/r/now/jitsi?ref=feature', headers=GROUPS)
    assert response.status_code == 200


@pytest.mark.parametrize('grep_status', ['failed', 'timeout'])
def test_repo_read_failure_is_unavailable_not_missing(client, grep_status):
    import app
    app.repo_managers['r'].grep_status = grep_status
    meeting_index.clear_cache()
    response = client.get('/api/meetings/r/now/jitsi', headers=GROUPS)
    assert response.status_code == 503 and response.json()['detail']['reason'] == 'unavailable'

import re
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import meeting_index as mi
import viewer_ui as ui
from test_meeting_index import FakeManager, entry, meeting_doc, memo, memo_file

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 15, 15, 30, tzinfo=KST)


def _profile(rid, collab=True):
    from synctx_common.config import CollabRepoConfig, RepoAuthConfig, RepoProfile
    return RepoProfile(id=rid, name=f"저장소 {rid}", url="https://git.test/r.git", auth=RepoAuthConfig(type="none"),
                       collab=CollabRepoConfig(enabled=collab))


@pytest.fixture
def setup(monkeypatch):
    from synctx_common.config import AuthzConfig, SynctxConfig, ViewerConfig
    import app as viewer_app
    cfg = SynctxConfig(authz=AuthzConfig(enabled=False), viewer=ViewerConfig(cdn_base_url="/cdn"),
                       repositories=[_profile("a"), _profile("b"), _profile("plain", collab=False)])
    monkeypatch.setattr(viewer_app, "config", cfg)
    mi.clear_cache()
    managers = {
        "a": FakeManager({
            "docs/past.md": meeting_doc(mid="past", title="지난 회의", starts="2020-01-01T10:00:00+09:00",
                                        extra="    presenters: [김]\n"),
            "docs/past.memo.md": memo_file([memo("decision", "채택<b>", mid="m_000001"),
                                            memo("action", "점검", mid="m_000002", owner="이", due="2020-02-01")],
                                           meeting_id="past"),
            "docs/future.md": meeting_doc(mid="future", title="다음 회의", starts="2099-01-01T10:00:00+09:00",
                                          channel="sec"),
            "docs/bad.md": meeting_doc(mid="bad", starts="내일"),
        }),
        "b": FakeManager({"x/cancel.md": meeting_doc(mid="c1", title="취소 회의", starts="2099-02-01T10:00:00+09:00",
                                                     extra="    cancelled: true\n")}),
        "plain": FakeManager({"p.md": meeting_doc(mid="p", title="비공개 회의")}),
    }
    for m in managers.values():
        m.profile.id = "x"
    monkeypatch.setattr(viewer_app, "repo_managers", managers)
    return TestClient(viewer_app.app, raise_server_exceptions=False)


class TestApi:
    def test_lists_only_collab_repos(self, setup):
        data = setup.get("/api/meetings").json()
        assert {m["repo_id"] for m in data["meetings"]} == {"a", "b"}
        assert [r["id"] for r in data["repos"]] == ["a", "b"]

    def test_sorted_and_shape(self, setup):
        data = setup.get("/api/meetings").json()
        assert [m["id"] for m in data["meetings"]] == ["past", "future", "c1"]
        past = data["meetings"][0]
        assert past["status"] == "ended" and past["starts_at"] == "2020-01-01T10:00:00+09:00"
        assert past["url"] == "/view/a/docs/past.md" and past["present_url"] == "/view/a/docs/past.md?mode=present"
        assert past["decisions"][0]["text"] == "채택<b>"
        assert past["actions"][0] == {"text": "점검", "owner": "이", "due": "2020-02-01", "quote": ""}
        assert data["invalid"] == [{"repo_id": "a", "repo_name": "저장소 a", "path": "docs/bad.md",
                                    "reason": data["invalid"][0]["reason"]}]

    def test_filters(self, setup):
        ids = lambda **p: [m["id"] for m in setup.get("/api/meetings", params=p).json()["meetings"]]
        assert ids(status="upcoming") == ["future"]
        assert ids(status="cancelled") == ["c1"]
        assert ids(repo="b") == ["c1"]
        assert ids(channel="sec") == ["future"]
        assert ids(presenter="김") == ["past"]
        assert ids(q="다음") == ["future"]
        assert ids(**{"from": "2099-01-01", "to": "2099-01-31"}) == ["future"]
        assert ids(order="desc") == ["c1", "future", "past"]

    def test_invalid_status_400(self, setup):
        assert setup.get("/api/meetings", params={"status": "weird"}).status_code == 400

    def test_unknown_or_non_collab_repo_404(self, setup):
        assert setup.get("/api/meetings", params={"repo": "plain"}).status_code == 404
        assert setup.get("/api/meetings", params={"repo": "nope"}).status_code == 404

    def test_unavailable_repo_reported(self, setup, monkeypatch):
        import app as viewer_app
        viewer_app.repo_managers["b"].status = "cloning"
        data = setup.get("/api/meetings").json()
        assert {r["id"]: r["status"] for r in data["repos"]} == {"a": "ok", "b": "unavailable"}

    def test_requires_viewer_role_when_authz_enabled(self, setup, monkeypatch):
        import app as viewer_app
        monkeypatch.setattr(viewer_app.config.authz, "enabled", True)
        assert setup.get("/api/meetings").status_code == 403
        assert setup.get("/meetings").status_code == 403


class TestServiceToken:
    TOKEN = "s3cret-token"

    @pytest.fixture
    def locked(self, setup, monkeypatch):
        import app as viewer_app
        monkeypatch.setattr(viewer_app.config.authz, "enabled", True)
        monkeypatch.setenv("VIEWER_SERVICE_TOKEN", self.TOKEN)
        return setup

    def get(self, client, path="/api/meetings", token=None, scheme="Bearer"):
        headers = {"Authorization": f"{scheme} {token}"} if token is not None else {}
        return client.get(path, headers=headers)

    def test_valid_token_opens_meetings_api_without_group_header(self, locked):
        resp = self.get(locked, token=self.TOKEN)
        assert resp.status_code == 200 and resp.json()["meetings"]

    @pytest.mark.parametrize("token", ["wrong", "", "s3cret-token-x", "s3cret-toke"])
    def test_wrong_token_rejected(self, locked, token):
        assert self.get(locked, token=token).status_code == 403

    def test_non_bearer_scheme_rejected(self, locked):
        assert self.get(locked, token=self.TOKEN, scheme="Basic").status_code == 403

    def test_token_does_not_open_other_endpoints(self, locked):
        for path in ("/api/repos", "/meetings", "/api/search?q=abc"):
            assert self.get(locked, path, token=self.TOKEN).status_code == 403

    def test_unset_token_never_matches(self, setup, monkeypatch):
        import app as viewer_app
        monkeypatch.setattr(viewer_app.config.authz, "enabled", True)
        monkeypatch.delenv("VIEWER_SERVICE_TOKEN", raising=False)
        assert self.get(setup, token="").status_code == 403
        assert self.get(setup, token="anything").status_code == 403

    def test_empty_token_env_never_matches(self, setup, monkeypatch):
        import app as viewer_app
        monkeypatch.setattr(viewer_app.config.authz, "enabled", True)
        monkeypatch.setenv("VIEWER_SERVICE_TOKEN", "")
        assert self.get(setup, token="").status_code == 403

    def test_group_header_still_works(self, locked, monkeypatch):
        import app as viewer_app
        monkeypatch.setattr(viewer_app.config.authz.role_mappings, "viewer", ["viewers"])
        resp = locked.get("/api/meetings", headers={"X-Forwarded-Groups": "viewers"})
        assert resp.status_code == 200

    def test_env_name_follows_config(self, setup, monkeypatch):
        import app as viewer_app
        monkeypatch.setattr(viewer_app.config.authz, "enabled", True)
        monkeypatch.setattr(viewer_app.config.collab.gateway, "viewer_service_token_env", "CUSTOM_TOKEN")
        monkeypatch.setenv("CUSTOM_TOKEN", "abc")
        assert self.get(setup, token="abc").status_code == 200
        monkeypatch.setenv("VIEWER_SERVICE_TOKEN", "zzz")
        assert self.get(setup, token="zzz").status_code == 403


class TestPage:
    def test_meetings_route_not_swallowed_by_repo_route(self, setup):
        resp = setup.get("/meetings")
        assert resp.status_code == 200 and "<h1>회의</h1>" in resp.text

    def test_tabs_counts_and_rows(self, setup):
        html = setup.get("/meetings").text
        assert 'aria-label="회의 보기"' in html
        assert re.search(r'aria-current="page">전체 <span class="meetings-count">3</span>', html)
        assert "지난 회의" in html and "다음 회의" in html and "비공개 회의" not in html

    def test_status_tab_shows_only_that_status(self, setup):
        html = setup.get("/meetings", params={"tab": "upcoming"}).text
        assert "다음 회의" in html and "지난 회의" not in html

    def test_memo_text_escaped(self, setup):
        html = setup.get("/meetings", params={"tab": "decisions"}).text
        assert "채택&lt;b&gt;" in html and "채택<b>" not in html

    def test_actions_tab_with_overdue_and_owner_filter(self, setup):
        html = setup.get("/meetings", params={"tab": "actions"}).text
        assert "점검" in html and "기한 지남" in html
        assert "점검" not in setup.get("/meetings", params={"tab": "actions", "owner": "박"}).text

    def test_invalid_listed_under_check_needed(self, setup):
        html = setup.get("/meetings").text
        assert "확인 필요" in html and "docs/bad.md" in html

    def test_unknown_repo_filter_404(self, setup):
        assert setup.get("/meetings", params={"repo": "plain"}).status_code == 404

    def test_header_link_shown_when_collab_repo_exists(self, setup):
        assert 'class="shell-link" href="/view/meetings"' in setup.get("/").text

    def test_invalid_dates_dropped_from_form(self, setup):
        html = setup.get("/meetings", params={"from": "xx"}).text
        assert 'name="from" value=""' in html


class TestUiRender:
    def render(self, meetings, tab="all", **kw):
        args = dict(view_prefix="/view", tab=tab, query={}, choices={"repos": [], "channels": [], "presenters": [], "owners": []},
                    meetings=meetings, invalid=[], repo_notes=[], truncated=False, now=NOW, has_repos=True)
        args.update(kw)
        return ui.render_meetings_page(**args)

    def test_no_collab_repos_empty_state(self):
        html = self.render([], has_repos=False)
        assert "collab.enabled" in html and "meetings-tabs" not in html

    def test_status_label_has_text_not_color_only(self):
        html = self.render([entry(cancelled=True)])
        assert ">취소</span>" in html

    def test_title_escaped(self):
        html = self.render([entry(title='<img src=x onerror=alert(1)>')])
        assert "<img" not in html and "&lt;img" in html

    def test_repo_notes_and_truncated(self):
        html = self.render([], repo_notes=[("저장소 a", "timeout")], truncated=True)
        assert "저장소 a: " in html and ui.MEETINGS_TRUNCATED_NOTE in html

    def test_unknown_tab_falls_back_to_all(self):
        assert 'aria-current="page">전체' in self.render([entry()], tab="bogus")

    def test_decisions_and_actions_render(self):
        item = mi.MemoItem("decision", "결정 A", "", "", "")
        act = mi.MemoItem("action", "할 일", "김", "2099-01-01", "")
        html = self.render([entry(decisions=[item], actions=[act])], tab="decisions")
        assert "결정 A" in html
        html = self.render([entry(decisions=[item], actions=[act])], tab="actions")
        assert "할 일" in html and "기한 지남" not in html

    def test_tab_links_keep_filters(self):
        html = self.render([entry()], query={"repo": "a", "q": "한글"})
        assert 'href="/view/meetings?repo=a&amp;q=%ED%95%9C%EA%B8%80&amp;tab=upcoming"' in html
        assert 'href="/view/meetings?repo=a&amp;q=%ED%95%9C%EA%B8%80"' in html

    def test_card_dashboard_link(self):
        data = {"title": "t", "time_label": "x", "starts_at_iso": "x", "duration_minutes": 1, "presenters": [],
                "channel": "c", "cancelled": False, "agenda": []}
        assert "회의 대시보드로" in ui.render_meeting_card(data, dashboard_href="/view/meetings")
        assert "회의 대시보드로" not in ui.render_meeting_card(data)

    def test_header_link_off_by_default(self):
        html = ui.render_header(view_prefix="/view", repos=[], theme="github-light")
        assert "shell-link" not in html

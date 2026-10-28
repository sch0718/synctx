from datetime import datetime, timedelta

import pytest

import commands
from helpers import KST, item, meeting

BASE = "https://synctx.synctx.local"
NOW = datetime(2026, 10, 15, 12, 0, tzinfo=KST)
KEYS = {"dx"}


def at(**kw):
    return NOW + timedelta(**kw)


def run(text, meetings, keys=KEYS, now=NOW):
    return commands.handle(text, meetings, keys, now, BASE)


class TestParse:
    def test_aliases_and_args(self):
        assert commands.parse("오늘") == ("today", "")
        assert commands.parse("today") == ("today", "")
        assert commands.parse("예정 14일") == ("upcoming", "14일")
        assert commands.parse("  회의   dx 회의  ") == ("meeting", "dx 회의")
        assert commands.parse("정리 m1") == ("summary", "m1")
        assert commands.parse("MEETING m1") == ("meeting", "m1")

    def test_empty_and_help(self):
        assert commands.parse("") == ("help", "")
        assert commands.parse("도움말") == ("help", "")
        assert commands.parse("?") == ("help", "")

    def test_unknown(self):
        assert commands.parse("미지원 키워드") == ("unknown", "미지원 키워드")


class TestHelp:
    def test_lists_supported_commands(self):
        reply = run("도움말", [])
        assert reply.command == "help" and reply.result == "ok"
        for word in ("오늘", "예정", "회의", "정리", "검색", "근거"):
            assert word in reply.text
        assert "참여 [id]" in reply.text

    def test_unknown_command_gets_help_hint(self):
        reply = run("미지원 abc", [])
        assert reply.command == "unknown" and reply.result == "invalid" and "도움말" in reply.text


class TestToday:
    def test_lists_todays_meetings_in_order_with_status(self):
        ended = meeting(id="a", title="오전 회의", starts_at=at(hours=-3), duration_minutes=60)
        ongoing = meeting(id="b", title="진행 회의", starts_at=at(minutes=-10), duration_minutes=60)
        later = meeting(id="c", title="오후 회의", starts_at=at(hours=3), duration_minutes=60)
        tomorrow = meeting(id="d", title="내일 회의", starts_at=at(days=1))
        reply = run("오늘", [later, tomorrow, ended, ongoing])
        assert reply.result == "ok"
        text = reply.text
        assert text.index("오전 회의") < text.index("진행 회의") < text.index("오후 회의")
        assert "내일 회의" not in text
        assert "종료" in text and "진행 중" in text and "예정" in text

    def test_cancelled_marked(self):
        reply = run("오늘", [meeting(title="취소됨", starts_at=at(hours=2), cancelled=True)])
        assert "취소" in reply.text

    def test_empty(self):
        reply = run("오늘", [meeting(starts_at=at(days=3))])
        assert reply.result == "ok" and "오늘" in reply.text and "없습니다" in reply.text

    def test_links_use_public_base_url(self):
        reply = run("오늘", [meeting(starts_at=at(hours=1))])
        assert f"({BASE}/view/r/docs/m.md)" in reply.text

    def test_today_uses_kst_date_boundary(self):
        late = datetime(2026, 10, 15, 23, 30, tzinfo=KST)
        just_after = datetime(2026, 10, 16, 0, 10, tzinfo=KST)
        reply = run("오늘", [meeting(id="x", title="늦은 회의", starts_at=late),
                             meeting(id="y", title="자정 후", starts_at=just_after)])
        assert "늦은 회의" in reply.text and "자정 후" not in reply.text


class TestUpcoming:
    def test_default_seven_days_excludes_past_and_far(self):
        near = meeting(id="a", title="가까운", starts_at=at(days=2))
        far = meeting(id="b", title="먼", starts_at=at(days=8))
        past = meeting(id="c", title="지난", starts_at=at(days=-1))
        text = run("예정", [far, near, past]).text
        assert "가까운" in text and "먼" not in text and "지난" not in text

    def test_ongoing_included(self):
        assert "진행중회의" in run("예정", [meeting(title="진행중회의", starts_at=at(minutes=-5))]).text

    def test_days_argument_variants(self):
        m = meeting(starts_at=at(days=10))
        assert "DX 회의" in run("예정 14", [m]).text
        assert "DX 회의" in run("예정 14일", [m]).text
        assert "DX 회의" not in run("예정 3", [m]).text

    def test_groups_by_date_in_order(self):
        a = meeting(id="a", title="이틀 뒤", starts_at=at(days=2))
        b = meeting(id="b", title="하루 뒤", starts_at=at(days=1))
        text = run("예정", [a, b]).text
        assert text.index("하루 뒤") < text.index("이틀 뒤")

    def test_cancelled_shown_with_mark(self):
        assert "취소" in run("예정", [meeting(starts_at=at(days=1), cancelled=True)]).text

    def test_invalid_days_rejected(self):
        for arg in ("0", "31", "abc", "-1", "1.5"):
            reply = run(f"예정 {arg}", [])
            assert reply.result == "invalid" and "1~30" in reply.text

    def test_empty_message_mentions_days(self):
        assert "7일" in run("예정", []).text

    def test_line_limit(self):
        many = [meeting(id=f"m{i}", title=f"회의 {i}", starts_at=at(hours=i + 1)) for i in range(25)]
        text = run("예정 30", many).text
        assert text.count("\n- ") == commands.LINE_LIMIT + 1 and "… 외 5건" in text


class TestChannelScope:
    def test_only_meetings_of_mapped_channel_keys(self):
        mine = meeting(id="a", title="내 채널", channel="dx", starts_at=at(hours=1))
        other = meeting(id="b", title="다른 채널", channel="sec", starts_at=at(hours=1))
        text = run("오늘", [mine, other]).text
        assert "내 채널" in text and "다른 채널" not in text

    def test_no_mapping_gives_no_channel_and_leaks_nothing(self):
        reply = run("오늘", [meeting(title="비밀", starts_at=at(hours=1))], keys=set())
        assert reply.result == "no_channel" and "비밀" not in reply.text and "연결" in reply.text

    def test_other_channel_meeting_not_found_by_id(self):
        other = meeting(id="secret", title="다른", channel="sec")
        reply = run("회의 secret", [other])
        assert reply.result == "not_found" and "다른" not in reply.text

    def test_multiple_keys_for_one_chat_channel(self):
        a = meeting(id="a", title="가", channel="dx", starts_at=at(hours=1))
        b = meeting(id="b", title="나", channel="ops", starts_at=at(hours=2))
        text = run("오늘", [a, b], keys={"dx", "ops"}).text
        assert "가" in text and "나" in text


class TestMeetingCard:
    def test_by_exact_id(self):
        m = meeting(id="dx-1", title="카드 회의", decisions=(item("결정"),), actions=(item("할 일"), item("또")))
        reply = run("회의 dx-1", [m, meeting(id="dx-2", title="카드 회의 둘")])
        assert reply.result == "ok" and "카드 회의" in reply.text
        assert "결정 1건" in reply.text and "액션 2건" in reply.text
        assert f"({BASE}/view/r/docs/m.md)" in reply.text and "mode=present" in reply.text

    def test_by_title_substring_case_insensitive(self):
        m = meeting(id="x", title="Backup Review")
        assert run("회의 backup", [m]).result == "ok"

    def test_ambiguous_lists_candidates_up_to_five(self):
        ms = [meeting(id=f"m{i}", title=f"점검 회의 {i}") for i in range(8)]
        reply = run("회의 점검", ms)
        assert reply.result == "ambiguous" and reply.text.count("\n- `") == 5 and "… 외 3건" in reply.text and "m0" in reply.text

    def test_exact_id_wins_over_title_matches(self):
        ms = [meeting(id="m1", title="일반"), meeting(id="m2", title="m1 이야기")]
        assert run("회의 m1", ms).result == "ok"

    def test_not_found_and_missing_argument(self):
        assert run("회의 없는거", [meeting()]).result == "not_found"
        assert run("회의", [meeting()]).result == "invalid"

    def test_escapes_title(self):
        reply = run("회의 x", [meeting(id="x", title="@here [a](http://evil)")])
        assert "@here" not in reply.text and "](http://evil" not in reply.text

    def test_status_shown(self):
        assert "취소" in run("회의 m1", [meeting(id="m1", cancelled=True)]).text


class TestSummary:
    def test_shows_decisions_and_actions(self):
        m = meeting(id="m1", decisions=(item("채택"),), actions=(item("점검", "김", "2026-10-20"),))
        reply = run("정리 m1", [m])
        assert reply.result == "ok" and "채택" in reply.text and "점검" in reply.text and "담당 김" in reply.text

    def test_empty_explains_memo_file(self):
        reply = run("정리 m1", [meeting(id="m1")])
        assert reply.result == "ok" and "memo.md" in reply.text

    def test_memo_error_is_mentioned_without_leaking_it_verbatim_markup(self):
        reply = run("정리 m1", [meeting(id="m1", memo_error="깨진 <b>파일</b>")])
        assert "읽지 못" in reply.text and "<b>" not in reply.text

    def test_requires_argument(self):
        assert run("정리", [meeting()]).result == "invalid"

    def test_memo_text_escaped(self):
        reply = run("정리 m1", [meeting(id="m1", decisions=(item("@all 공지"),))])
        assert "@all" not in reply.text


class TestInputValidation:
    def test_too_long_text_rejected(self):
        reply = run("회의 " + "가" * 300, [meeting()])
        assert reply.result == "invalid"

    def test_control_characters_flattened(self):
        assert commands.parse("회의\x00 m1\n") == ("meeting", "m1")

    def test_reply_text_is_bounded(self):
        ms = [meeting(id=f"m{i}", title="가" * 190, starts_at=at(hours=1 + i % 20)) for i in range(25)]
        assert len(run("예정", ms).text) <= commands.MAX_REPLY_CHARS


class TestSearchAndEvidence:
    @pytest.mark.parametrize("text,command,arg", [
        ("검색 규정", "search", "규정"), ("search audit", "search", "audit"),
        ("search\taudit log", "search", "audit log"),
        ("근거 m1", "evidence", "m1"), ("evidence 회의", "evidence", "회의"),
    ])
    def test_aliases(self, text, command, arg):
        assert commands.parse(text) == (command, arg)

    @pytest.mark.parametrize("query", ["", "a", "x" * 101, "ab\ncd", "ab\rcd", "ab\x00cd"])
    def test_search_validation_before_lookup(self, query):
        reply = run("검색 " + query, [meeting()])
        assert reply.result == "invalid" and reply.lookup is None

    @pytest.mark.parametrize("query", ["ab", "x" * 100, "  규정 검색  ", "audit\tlog"])
    def test_valid_query_matches_viewer_normalization(self, query):
        reply = run("검색 " + query, [meeting()])
        assert reply.lookup.query == query.strip()

    def test_lookup_is_pure_and_channel_scoped(self):
        mine = meeting(id="m1")
        assert run("검색 규정", [mine]).lookup.query == "규정"
        assert run("근거 m1", [mine]).lookup.meeting == mine
        assert run("근거 secret", [meeting(id="secret", channel="sec")]).result == "not_found"
        assert run("검색 규정", [meeting(channel="sec")]).result == "no_channel"
        assert run("근거", [mine]).result == "invalid"
        assert run("근거 회의", [mine, meeting(id="m2")]).result == "ambiguous"

    def test_search_filters_before_limit_and_escapes_paths(self):
        from models import DocumentLink, SearchResult
        other = SearchResult("other", "failed", ())
        docs = tuple(DocumentLink("r", f"@all [{i}].md", f"@all [{i}].md", f"/view/r/{i}.md") for i in range(8))
        data = [other, SearchResult("r", "ok", (docs[0], *docs))]
        reply = commands.handle("검색 규정", [meeting()], {"dx"}, NOW, BASE, search_results=data)
        assert reply.result == "ok" and reply.text.count("\n- [") == 5
        assert "… 외 3건" in reply.text
        assert "@all" not in reply.text and "\\[0\\]" in reply.text
        assert f"({BASE}/view/r/4.md)" in reply.text and "/view/r/5.md" not in reply.text

    def test_search_multiple_repos_for_mapped_meetings(self):
        from models import DocumentLink, SearchResult
        results = [SearchResult(rid, "ok", (DocumentLink(rid, "a.md", rid, f"/view/{rid}/a.md"),))
                   for rid in ("r", "r2", "secret")]
        ms = [meeting(), meeting(repo_id="r2", channel="ops"), meeting(repo_id="secret", channel="sec")]
        reply = commands.handle("search audit", ms, {"dx", "ops"}, NOW, BASE, search_results=results)
        assert "/view/r/" in reply.text and "/view/r2/" in reply.text and "secret" not in reply.text

    def test_empty_and_failed_search(self):
        from models import SearchResult
        assert "없습니다" in commands.handle("검색 규정", [meeting()], {"dx"}, NOW, BASE, search_results=[]).text
        reply = commands.handle("검색 규정", [meeting()], {"dx"}, NOW, BASE,
                                search_results=[SearchResult("r", "timeout", ())])
        assert reply.result == "unavailable"
        assert "지금 검색 결과를 읽지 못했습니다" in reply.text

    def test_evidence_limit_escape_and_empty(self):
        from models import DocumentLink, EvidenceResult
        data = [DocumentLink("r", "a.md", f"@here [근거{i}]", f"/view/r/{i}.md") for i in range(22)]
        reply = commands.handle("근거 m1", [meeting(id="m1")], {"dx"}, NOW, BASE, evidence_results=EvidenceResult(tuple(data)))
        assert reply.text.count("\n- [") == 20 and "@here" not in reply.text and "\\[근거0\\]" in reply.text
        assert "… 외 2건" in reply.text
        assert f"({BASE}/view/r/19.md)" in reply.text and "/view/r/20.md" not in reply.text
        reply = commands.handle("근거 m1", [meeting(id="m1")], {"dx"}, NOW, BASE, evidence_results=EvidenceResult(()))
        assert "근거가 없습니다" in reply.text

    @pytest.mark.parametrize("with_document", [False, True])
    def test_partial_evidence_does_not_claim_no_evidence(self, with_document):
        from models import DocumentLink, EvidenceResult
        docs = (DocumentLink("r", "a.md", "규정", "/view/r/a.md"),) if with_document else ()
        reply = commands.handle("근거 m1", [meeting(id="m1")], {"dx"}, NOW, BASE,
                                evidence_results=EvidenceResult(docs, "partial", 3))
        assert "일부 근거를 읽지 못했습니다(3건 생략)" in reply.text
        assert "근거가 없습니다" not in reply.text


class TestJoin:
    def test_alias_and_channel_scope(self):
        assert commands.parse('JOIN m1') == ('join', 'm1')
        assert commands.parse('참여') == ('join', '')
        reply = run('참여 other', [meeting(id='other', channel='secret')])
        assert reply.result == 'not_found'
        assert run('join', [], keys=set()).result == 'no_channel'

    def test_defaults_to_ongoing_then_nearest_upcoming(self):
        ongoing = meeting(id='now', starts_at=at(minutes=-10), title='지금')
        upcoming = meeting(id='next', starts_at=at(minutes=10), title='다음')
        far = meeting(id='far', starts_at=at(days=1), title='먼 회의')
        cancelled = meeting(id='cancel', starts_at=at(minutes=1), cancelled=True)
        assert '**지금**' in run('join', [far, upcoming, ongoing]).text
        assert '**다음**' in run('join', [far, upcoming, cancelled]).text
        assert run('join', [meeting(starts_at=at(days=-1)), cancelled]).result == 'not_found'
        assert run('join', []).result == 'not_found'

    def test_explicit_id_resolves_ambiguity(self):
        assert run('참여 m1', [meeting(), meeting(repo_id='b')]).result == 'ambiguous'
        assert run('참여 없는회의', [meeting()]).result == 'not_found'

    def test_only_document_links_and_no_credentials(self):
        m = meeting(starts_at=at(minutes=-5), jitsi_enabled=True,
                    joinable_from=at(minutes=-20), joinable_until=at(hours=2))
        reply = run('참여 m1', [m])
        assert reply.result == 'ok' and reply.command == 'join'
        assert '문서 화면의 참여 버튼으로 입장' in reply.text
        assert BASE + m.url in reply.text and BASE + m.present_url in reply.text
        assert all(needle not in reply.text.casefold() for needle in ('jwt', 'room', 'token', '/jitsi', 'external_api'))

    @pytest.mark.parametrize('changes,expected', [({'cancelled': True}, '취소'),
                                                ({'jitsi_enabled': False}, '꺼져'),
                                                ({'joinable_from': at(minutes=1)}, '15분 전'),
                                                ({'joinable_until': NOW}, '1시간')])
    def test_unavailable_reasons(self, changes, expected):
        args = dict(jitsi_enabled=True, joinable_from=at(minutes=-15), joinable_until=at(hours=2))
        args.update(changes)
        assert expected in run('참여 m1', [meeting(**args)]).text

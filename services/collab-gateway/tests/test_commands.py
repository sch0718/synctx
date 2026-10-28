from datetime import datetime, timedelta

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
        assert commands.parse("검색 키워드") == ("unknown", "검색 키워드")


class TestHelp:
    def test_lists_commands_and_not_deferred_ones(self):
        reply = run("도움말", [])
        assert reply.command == "help" and reply.result == "ok"
        for word in ("오늘", "예정", "회의", "정리"):
            assert word in reply.text
        assert "검색" not in reply.text

    def test_unknown_command_gets_help_hint(self):
        reply = run("검색 abc", [])
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

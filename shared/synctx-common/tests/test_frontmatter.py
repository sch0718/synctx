import pytest
from datetime import date, datetime, timezone, timedelta

from synctx_common.frontmatter import FrontMatter, SynctxFrontMatter, parse_frontmatter, validate_frontmatter, MeetingFrontMatter, validate_meeting


class TestParseFrontmatter:
    def test_basic(self):
        content = "---\ntitle: Hello\n---\nBody text"
        fm, body = parse_frontmatter(content)
        assert fm == {"title": "Hello"}
        assert body == "Body text"

    def test_no_frontmatter(self):
        content = "Just body text"
        fm, body = parse_frontmatter(content)
        assert fm == {}
        assert body == "Just body text"

    def test_synctx_metadata(self):
        content = "---\ntitle: Task\nsynctx:\n  type: task\n  status: open\n  priority: high\n---\n# Content"
        fm, body = parse_frontmatter(content)
        assert fm["synctx"]["type"] == "task"
        assert fm["synctx"]["status"] == "open"
        assert body == "# Content"

    def test_marp_flag(self):
        content = "---\nmarp: true\ntitle: Slides\n---\n# Slide 1"
        fm, body = parse_frontmatter(content)
        assert fm["marp"] is True

    def test_invalid_yaml(self):
        content = "---\n: [invalid yaml\n---\nBody"
        fm, body = parse_frontmatter(content)
        assert fm == {}
        assert body == "Body"

    def test_non_dict_yaml(self):
        content = "---\n- item1\n- item2\n---\nBody"
        fm, body = parse_frontmatter(content)
        assert fm == {}

    def test_empty_frontmatter(self):
        content = "---\n\n---\nBody"
        fm, body = parse_frontmatter(content)
        assert fm == {} or fm is None
        assert "Body" in body

    def test_multiline_values(self):
        content = "---\ntitle: Hello World\ndescription: |\n  Line 1\n  Line 2\n---\nBody"
        fm, body = parse_frontmatter(content)
        assert fm["title"] == "Hello World"
        assert "Line 1" in fm["description"]


class TestValidateFrontmatter:
    def test_valid_synctx(self):
        fm = validate_frontmatter({
            "title": "Test Task",
            "synctx": {"type": "task", "status": "open", "priority": "high"}
        })
        assert isinstance(fm, FrontMatter)
        assert fm.title == "Test Task"
        assert fm.synctx.type == "task"
        assert fm.synctx.status == "open"

    def test_empty_dict(self):
        fm = validate_frontmatter({})
        assert fm.title is None
        assert fm.synctx is None

    def test_none_input(self):
        fm = validate_frontmatter(None)
        assert fm.title is None

    def test_issues_list(self):
        fm = validate_frontmatter({
            "synctx": {"type": "version", "issues": ["TASK-001", "TASK-002"]}
        })
        assert fm.synctx.issues == ["TASK-001", "TASK-002"]

    def test_all_synctx_fields(self):
        fm = validate_frontmatter({
            "synctx": {
                "type": "task",
                "target": "openproject",
                "subject": "Fix bug",
                "status": "in_progress",
                "priority": "high",
                "version": "v0.1.0",
                "due_date": "2026-07-01",
                "assignee": "user1",
                "name": "task-001",
                "issues": [],
            }
        })
        assert fm.synctx.target == "openproject"
        assert fm.synctx.due_date == "2026-07-01"

    def test_unknown_fields_ignored(self):
        fm = validate_frontmatter({"title": "Test", "unknown_field": "value"})
        assert fm.title == "Test"


def _fm(**meeting):
    base = {
        "id": "dx-ic-21-review-2026-10-08",
        "title": "팀 문서 21번 검토",
        "starts_at": "2026-10-08T14:00+09:00",
        "duration_minutes": 60,
        "channel": "team-docs",
    }
    base.update(meeting)
    base = {k: v for k, v in base.items() if v is not _MISSING}
    return {"synctx": {"type": "meeting", "meeting": base}}


_MISSING = object()


class TestValidateMeeting:
    def test_valid_string_starts_at(self):
        meeting, errors = validate_meeting(_fm())
        assert errors == []
        assert isinstance(meeting, MeetingFrontMatter)
        assert meeting.starts_at.utcoffset() == timedelta(hours=9)
        assert meeting.presenters == []
        assert meeting.cancelled is False

    def test_valid_datetime_starts_at_from_yaml(self):
        tz = timezone(timedelta(hours=9))
        meeting, errors = validate_meeting(_fm(starts_at=datetime(2026, 10, 8, 14, 0, tzinfo=tz)))
        assert errors == []
        assert meeting.starts_at.hour == 14

    def test_zulu_time_accepted(self):
        meeting, errors = validate_meeting(_fm(starts_at="2026-10-08T05:00:00Z"))
        assert errors == []
        assert meeting.starts_at.utcoffset() == timedelta(0)

    def test_missing_meeting_block(self):
        meeting, errors = validate_meeting({"synctx": {"type": "meeting"}})
        assert meeting is None
        assert errors == ["synctx.meeting 블록이 없습니다"]

    def test_missing_id(self):
        meeting, errors = validate_meeting(_fm(id=_MISSING))
        assert meeting is None
        assert "meeting.id가 없습니다" in errors

    def test_bad_id_format(self):
        _, errors = validate_meeting(_fm(id="회의 21"))
        assert any("meeting.id 형식" in e for e in errors)

    def test_missing_title(self):
        _, errors = validate_meeting(_fm(title="  "))
        assert "meeting.title이 없습니다" in errors

    def test_naive_datetime_rejected(self):
        _, errors = validate_meeting(_fm(starts_at="2026-10-08T14:00"))
        assert "starts_at에 타임존이 없습니다(예: 2026-10-08T14:00+09:00)" in errors

    def test_naive_datetime_object_rejected(self):
        _, errors = validate_meeting(_fm(starts_at=datetime(2026, 10, 8, 14, 0)))
        assert "starts_at에 타임존이 없습니다(예: 2026-10-08T14:00+09:00)" in errors

    def test_date_only_string_rejected(self):
        _, errors = validate_meeting(_fm(starts_at="2026-10-08"))
        assert "starts_at에 시각이 없습니다(예: 2026-10-08T14:00+09:00)" in errors

    def test_date_object_rejected(self):
        _, errors = validate_meeting(_fm(starts_at=date(2026, 10, 8)))
        assert "starts_at에 시각이 없습니다(예: 2026-10-08T14:00+09:00)" in errors

    def test_garbage_starts_at(self):
        _, errors = validate_meeting(_fm(starts_at="next tuesday"))
        assert "starts_at 형식이 올바르지 않습니다(ISO 8601)" in errors

    def test_missing_starts_at(self):
        _, errors = validate_meeting(_fm(starts_at=_MISSING))
        assert "starts_at이 없습니다" in errors

    @pytest.mark.parametrize("value", [0, 1441, "60", True])
    def test_duration_out_of_range_or_wrong_type(self, value):
        _, errors = validate_meeting(_fm(duration_minutes=value))
        assert "duration_minutes는 1~1440 사이의 정수여야 합니다" in errors

    def test_duration_default(self):
        meeting, errors = validate_meeting(_fm(duration_minutes=_MISSING))
        assert errors == []
        assert meeting.duration_minutes == 60

    @pytest.mark.parametrize("value", ["DX-Channel", "-dx", "a" * 65, "dx channel"])
    def test_bad_channel(self, value):
        _, errors = validate_meeting(_fm(channel=value))
        assert any("channel 형식" in e for e in errors)

    def test_missing_channel(self):
        _, errors = validate_meeting(_fm(channel=_MISSING))
        assert "meeting.channel이 없습니다" in errors

    def test_id_with_trailing_newline_rejected(self):
        _, errors = validate_meeting(_fm(id="abc\n"))
        assert any("meeting.id 형식" in e for e in errors)

    def test_channel_with_trailing_newline_rejected(self):
        _, errors = validate_meeting(_fm(channel="dx-team\n"))
        assert any("channel 형식" in e for e in errors)

    def test_presenters_must_be_string_list(self):
        _, errors = validate_meeting(_fm(presenters=["sch0718", 3]))
        assert "presenters는 문자열 목록이어야 합니다" in errors

    def test_presenters_ok(self):
        meeting, errors = validate_meeting(_fm(presenters=["sch0718"]))
        assert errors == []
        assert meeting.presenters == ["sch0718"]

    def test_presenters_none_uses_default(self):
        meeting, errors = validate_meeting(_fm(presenters=None))
        assert errors == []
        assert meeting.presenters == []

    def test_cancelled_none_uses_default(self):
        meeting, errors = validate_meeting(_fm(cancelled=None))
        assert errors == []
        assert meeting.cancelled is False

    def test_cancelled_must_be_bool(self):
        _, errors = validate_meeting(_fm(cancelled="yes"))
        assert "cancelled는 true 또는 false여야 합니다" in errors

    def test_multiple_errors_reported(self):
        _, errors = validate_meeting(_fm(id=_MISSING, channel=_MISSING))
        assert len(errors) == 2

    def test_synctx_frontmatter_accepts_meeting(self):
        from synctx_common.frontmatter import validate_frontmatter
        fm = validate_frontmatter({"synctx": {"type": "meeting", "meeting": {
            "id": "m1", "title": "t", "starts_at": "2026-10-08T14:00:00+09:00", "channel": "c"}}})
        assert fm.synctx.meeting.id == "m1"

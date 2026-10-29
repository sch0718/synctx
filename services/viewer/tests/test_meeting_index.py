import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import meeting_index as mi
from synctx_common.git_manager import GrepMatch, GrepResult

KST = timezone(timedelta(hours=9))


def meeting_doc(mid="m1", title="회의", starts="2026-10-15T15:00:00+09:00", channel="dx", extra="", mtype="meeting"):
    return (
        f"---\nsynctx:\n  type: {mtype}\n  meeting:\n    id: {mid}\n    title: \"{title}\"\n"
        f"    starts_at: {starts}\n    channel: {channel}\n{extra}---\n\n# 본문\n"
    )


def memo_file(memos, meeting_id="m1"):
    payload = {"schema": 1, "doc": {"repo_id": "r", "meeting_id": meeting_id}, "exported_at": "2026-10-15T16:00:00+09:00",
               "memos": memos}
    return "# 회의 메모\n\n<!-- synctx-memo-data " + json.dumps(payload, ensure_ascii=False) + " -->\n"


def memo(kind, text, mid="m_abcdef", owner=None, due=None, deleted=None, quote=None):
    item = {"id": mid, "kind": kind, "text": text, "created_at": "2026-10-15T15:10:00+09:00",
            "updated_at": "2026-10-15T15:10:00+09:00"}
    if owner is not None:
        item["owner"] = owner
    if due is not None:
        item["due"] = due
    if deleted:
        item["deleted_at"] = deleted
    if quote:
        item["anchor"] = {"start": 0, "end": 1, "quote": quote}
    return item


class FakeManager:
    def __init__(self, files, *, status="ready", storage_mode="full", sha="a" * 40, grep_status="ok", grep_truncated=False):
        self.files = files
        self.status = status
        self.profile = SimpleNamespace(id="r", default_ref="main", storage_mode=storage_mode,
                                       collab=SimpleNamespace(enabled=True))
        self.sha = sha
        self.grep_status = grep_status
        self.grep_truncated = grep_truncated
        self.reads = []

    def resolve_ref(self, ref=None):
        return "origin/main"

    def resolve_commit_sha(self, ref=None):
        return self.sha

    def file_paths(self, ref=None):
        return set(self.files)

    def grep(self, query, ref=None, *, pathspecs=(), path_filter=None, limit=50, timeout=3.0, **_):
        matches = [GrepMatch(p, 1, "x") for p, t in sorted(self.files.items())
                   if query.lower() in t.lower() and (path_filter is None or path_filter(p))]
        return GrepResult(status=self.grep_status, matches=matches, truncated=self.grep_truncated)

    def blob_size_resolved(self, path, resolved):
        return len(self.files[path].encode())

    def read_resolved(self, path, resolved):
        self.reads.append(path)
        return self.files[path].encode()


@pytest.fixture(autouse=True)
def _clear_cache():
    mi.clear_cache()


def collect(files, **kw):
    return mi.collect("r", "Repo", FakeManager(files, **kw))


class TestCollect:
    def test_collects_valid_meeting_and_skips_other_docs(self):
        res = collect({"docs/m.md": meeting_doc(), "docs/other.md": "# 일반\nmeeting: 언급만\n",
                       "docs/doc.md": meeting_doc(mtype="doc")})
        assert res.status == "ok"
        assert [m.path for m in res.meetings] == ["docs/m.md"]
        m = res.meetings[0]
        assert (m.repo_id, m.repo_name, m.id, m.title, m.channel) == ("r", "Repo", "m1", "회의", "dx")
        assert m.starts_at == datetime(2026, 10, 15, 15, 0, tzinfo=KST)

    def test_invalid_meeting_reported_with_reason(self):
        res = collect({"docs/bad.md": meeting_doc(channel="''", starts="내일")})
        assert res.meetings == []
        assert res.invalid[0].path == "docs/bad.md"
        assert res.invalid[0].reason

    def test_unparsable_front_matter_only_invalidates_that_document(self):
        res = collect({"docs/bad.md": meeting_doc(mid="bad", starts="2026-10-32T15:00:00+09:00"),
                       "docs/ok.md": meeting_doc(mid="ok")})
        assert res.status == "ok"
        assert [m.id for m in res.meetings] == ["ok"]
        assert [i.path for i in res.invalid] == ["docs/bad.md"]

    def test_unavailable_and_unsupported(self):
        assert collect({}, status="cloning").status == "unavailable"
        assert collect({}, storage_mode="blobless").status == "unsupported"
        assert collect({}, storage_mode="treeless").status == "unsupported"

    def test_grep_timeout_status_propagates(self):
        assert collect({"a.md": meeting_doc()}, grep_status="timeout").status == "timeout"

    def test_hidden_paths_and_memo_files_not_candidates(self):
        res = collect({".hidden/m.md": meeting_doc(), "docs/m.memo.md": meeting_doc(mid="m2")})
        assert res.meetings == []

    def test_cached_by_commit_sha(self):
        mgr = FakeManager({"docs/m.md": meeting_doc()})
        mi.collect("r", "Repo", mgr)
        first_reads = len(mgr.reads)
        mi.collect("r", "Repo", mgr)
        assert len(mgr.reads) == first_reads
        mgr.sha = "b" * 40
        mi.collect("r", "Repo", mgr)
        assert len(mgr.reads) > first_reads

    def test_oversized_document_reported_not_read(self):
        big = meeting_doc() + "x" * mi.DOC_MAX_BYTES
        mgr = FakeManager({"docs/m.md": big})
        res = mi.collect("r", "Repo", mgr)
        assert mgr.reads == []
        assert res.invalid[0].path == "docs/m.md"

    def test_meeting_limit_marks_truncated(self, monkeypatch):
        monkeypatch.setattr(mi, "MAX_MEETINGS", 2)
        files = {f"docs/m{i}.md": meeting_doc(mid=f"m{i}") for i in range(3)}
        res = collect(files)
        assert len(res.meetings) == 2 and res.truncated is True

    def test_grep_truncated_propagates(self):
        assert collect({"a.md": meeting_doc()}, grep_truncated=True).truncated is True

    def test_reserved_meeting_attached_from_memo_file(self):
        files = {"docs/m.md": meeting_doc(),
                 "docs/m.memo.md": memo_file([memo("decision", "채택", mid="m_000001"),
                                              memo("action", "점검", mid="m_000002", owner="김", due="2026-10-20"),
                                              memo("note", "참고", mid="m_000003")])}
        m = collect(files).meetings[0]
        assert [d.text for d in m.decisions] == ["채택"]
        assert [(a.text, a.owner, a.due) for a in m.actions] == [("점검", "김", "2026-10-20")]
        assert m.memo_error is None


class TestMemoSummary:
    def test_deleted_and_invalid_memos_skipped(self):
        text = memo_file([memo("action", "삭제됨", deleted="2026-10-16T00:00:00+09:00"),
                          memo("action", "잘못된 기한", mid="m_000002", due="내일"),
                          memo("action", "유효", mid="m_000003", due="2026-10-20")])
        decisions, actions, error = mi.parse_memo_summary(text, "m1")
        assert decisions == [] and [a.text for a in actions] == ["유효"] and error is None

    def test_missing_marker(self):
        _, _, error = mi.parse_memo_summary("# 메모만\n", "m1")
        assert error

    def test_broken_json(self):
        _, _, error = mi.parse_memo_summary("<!-- synctx-memo-data {oops} -->", "m1")
        assert error

    def test_meeting_id_mismatch(self):
        _, _, error = mi.parse_memo_summary(memo_file([memo("action", "x")], meeting_id="other"), "m1")
        assert error

    def test_schema_mismatch(self):
        text = memo_file([memo("action", "x")]).replace('"schema": 1', '"schema": 2')
        assert mi.parse_memo_summary(text, "m1")[2]

    def test_size_limit(self):
        text = memo_file([memo("action", "x")]) + "y" * mi.MEMO_MAX_BYTES
        assert mi.parse_memo_summary(text, "m1")[2]

    def test_memo_count_limit(self, monkeypatch):
        monkeypatch.setattr(mi, "MEMO_MAX_COUNT", 2)
        text = memo_file([memo("action", "a", mid=f"m_00000{i}") for i in range(3)])
        assert mi.parse_memo_summary(text, "m1")[2]

    def test_escaped_markup_stays_text(self):
        text = memo_file([memo("decision", "<script>alert(1)</script>")])
        decisions, _, _ = mi.parse_memo_summary(text, "m1")
        assert decisions[0].text == "<script>alert(1)</script>"

    def test_text_longer_than_limit_skipped(self):
        text = memo_file([memo("decision", "가" * 10001)])
        assert mi.parse_memo_summary(text, "m1")[0] == []


def entry(**kw):
    base = dict(repo_id="r", repo_name="Repo", path="docs/m.md", id="m1", title="회의",
                starts_at=datetime(2026, 10, 15, 15, 0, tzinfo=KST), duration_minutes=60, channel="dx",
                presenters=[], cancelled=False, decisions=[], actions=[], memo_error=None)
    base.update(kw)
    return mi.MeetingEntry(**base)


class TestStatus:
    start = datetime(2026, 10, 15, 15, 0, tzinfo=KST)

    @pytest.mark.parametrize("delta,expected", [
        (timedelta(minutes=-1), "upcoming"), (timedelta(0), "ongoing"), (timedelta(minutes=59), "ongoing"),
        (timedelta(minutes=60), "ended"), (timedelta(days=1), "ended")])
    def test_boundaries(self, delta, expected):
        assert mi.status_of(entry(), self.start + delta) == expected

    def test_cancelled_wins(self):
        assert mi.status_of(entry(cancelled=True), self.start - timedelta(days=1)) == "cancelled"

    def test_other_timezone_now(self):
        now = datetime(2026, 10, 15, 6, 30, tzinfo=timezone.utc)
        assert mi.status_of(entry(), now) == "ongoing"


NOW = datetime(2026, 10, 15, 15, 30, tzinfo=KST)


class TestFilter:
    def setup_method(self):
        self.items = [
            entry(id="a", title="백업 점검", starts_at=datetime(2026, 10, 1, 10, 0, tzinfo=KST), presenters=["김"]),
            entry(id="b", title="보안 회의", repo_id="s", channel="sec", starts_at=datetime(2026, 10, 20, 10, 0, tzinfo=KST)),
            entry(id="c", title="취소됨", cancelled=True, starts_at=datetime(2026, 10, 25, 10, 0, tzinfo=KST)),
            entry(id="d", title="진행", starts_at=datetime(2026, 10, 15, 15, 0, tzinfo=KST)),
        ]

    def ids(self, **kw):
        return [m.id for m in mi.filter_meetings(self.items, now=NOW, **kw)]

    def test_no_filter_sorted_by_start(self):
        assert self.ids() == ["a", "d", "b", "c"]

    def test_status(self):
        assert self.ids(status="upcoming") == ["b"]
        assert self.ids(status="ongoing") == ["d"]
        assert self.ids(status="ended") == ["a"]
        assert self.ids(status="cancelled") == ["c"]

    def test_repo_channel_presenter(self):
        assert self.ids(repo="s") == ["b"]
        assert self.ids(channel="sec") == ["b"]
        assert self.ids(presenter="김") == ["a"]

    def test_text_search_case_insensitive(self):
        assert self.ids(q="백업") == ["a"]

    def test_date_range_kst_inclusive(self):
        assert self.ids(start=mi.parse_date("2026-10-15"), end=mi.parse_date("2026-10-20")) == ["d", "b"]

    def test_unknown_status_ignored(self):
        assert self.ids(status="weird") == ["a", "d", "b", "c"]

    def test_descending_order(self):
        assert self.ids(descending=True) == ["c", "b", "d", "a"]


class TestParseDate:
    def test_valid(self):
        assert mi.parse_date("2026-10-15") == datetime(2026, 10, 15, tzinfo=KST).date()

    @pytest.mark.parametrize("value", [None, "", "2026-13-01", "abc", "2026/10/15"])
    def test_invalid(self, value):
        assert mi.parse_date(value) is None


class TestActionItems:
    def test_sorted_by_due_then_without_due_last_and_owner_filter(self):
        a = mi.MemoItem(kind="action", text="늦음", owner="김", due="2026-10-30", quote="")
        b = mi.MemoItem(kind="action", text="빠름", owner="이", due="2026-10-20", quote="")
        c = mi.MemoItem(kind="action", text="기한 없음", owner="김", due="", quote="")
        items = mi.action_items([entry(actions=[a, c]), entry(id="z", actions=[b])])
        assert [i.item.text for i in items] == ["빠름", "늦음", "기한 없음"]
        assert [i.item.text for i in mi.action_items([entry(actions=[a, c, b])], owner="김")] == ["늦음", "기한 없음"]


class TestRealGit:
    def _git(self, cwd, *args):
        import subprocess
        env = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1", "PATH": __import__("os").environ["PATH"],
               "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=env)

    def test_collects_from_cloned_repository(self, tmp_path):
        import asyncio
        from synctx_common.config import RepoProfile
        from synctx_common.git_manager import GitRepoManager
        work = tmp_path / "work"
        (work / "docs").mkdir(parents=True)
        (work / "docs/m.md").write_text(meeting_doc(mid="real", title="실제 회의"), encoding="utf-8")
        (work / "docs/m.memo.md").write_text(memo_file([memo("decision", "채택", mid="m_000001")], meeting_id="real"),
                                             encoding="utf-8")
        (work / "docs/other.md").write_text("# 일반\n\nmeeting: 본문 언급\n", encoding="utf-8")
        self._git(tmp_path, "init", "-q", "-b", "main", str(work))
        self._git(work, "add", ".")
        self._git(work, "commit", "-qm", "init")
        manager = GitRepoManager(RepoProfile(id="r", name="r", url=work.as_uri()), str(tmp_path / "cache"))
        asyncio.run(manager.ensure())
        res = mi.collect("r", "Repo", manager)
        assert res.status == "ok"
        assert [(m.id, m.path) for m in res.meetings] == [("real", "docs/m.md")]
        assert [d.text for d in res.meetings[0].decisions] == ["채택"]
        assert res.invalid == []

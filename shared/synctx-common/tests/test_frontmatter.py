import pytest

from synctx_common.frontmatter import FrontMatter, SynctxFrontMatter, parse_frontmatter, validate_frontmatter


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

import re
import json
from pathlib import Path
from typing import List, Dict, Any, Tuple
import logging

from .config import SynctxConfig
from .frontmatter import parse_frontmatter

logger = logging.getLogger(__name__)

LINK_RE = re.compile(r'\[([^\]]*)\]\(([^)]+)\)')


class HarnessValidator:
    def __init__(self, config: SynctxConfig, workspace_root: str):
        self.config = config
        self.workspace_root = Path(workspace_root)
        self.reports_dir = self.workspace_root / config.harness.reports_path
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def validate(self) -> Dict[str, Any]:
        report = {
            "status": "success",
            "errors": [],
            "warnings": [],
            "files_checked": 0,
            "agent_readiness_failures": []
        }

        # 1. Check required context files
        context_dir = self.workspace_root / self.config.harness.context_path
        for req_file in self.config.harness.required_context_files:
            if not (context_dir / req_file).exists():
                report["errors"].append(f"Missing required context file: {self.config.harness.context_path}/{req_file}")

        # 2. Check all markdown files for agent-readiness & version consistency
        versions: Dict[str, List[str]] = {} # version_name -> list of issue paths
        tasks: Dict[str, str] = {} # task_path -> version_name

        for md_file in self.workspace_root.rglob("*.md"):
            if ".context" in md_file.parts or "node_modules" in md_file.parts or ".git" in md_file.parts:
                continue

            report["files_checked"] += 1
            rel_path = str(md_file.relative_to(self.workspace_root))
            content = md_file.read_text(encoding="utf-8")
            
            # Agent Readiness
            readiness_errors = self._check_agent_readiness(content)
            if self.config.harness.agent_readiness.check_links:
                link_errors = self._check_links(content, md_file)
                readiness_errors.extend(link_errors)
            if readiness_errors:
                report["agent_readiness_failures"].append({
                    "file": rel_path,
                    "errors": readiness_errors
                })

            # Parse frontmatter for consistency checks
            fm, _ = parse_frontmatter(content)
            if fm and "synctx" in fm:
                synctx_meta = fm["synctx"]
                doc_type = synctx_meta.get("type")
                
                if doc_type == "version":
                    v_name = synctx_meta.get("name", fm.get("title", rel_path))
                    issues = synctx_meta.get("issues", [])
                    versions[v_name] = issues
                elif doc_type == "task" or "version" in synctx_meta:
                    v_name = synctx_meta.get("version")
                    if v_name:
                        tasks[rel_path] = v_name

        # 3. Version / Task consistency check
        for task_path, v_name in tasks.items():
            if v_name in versions:
                if task_path not in versions[v_name]:
                    report["errors"].append(f"Task {task_path} claims version '{v_name}', but is not listed in the Version document's issues.")
            else:
                report["warnings"].append(f"Task {task_path} claims version '{v_name}', but no such Version document found.")

        for v_name, issues in versions.items():
            for issue_path in issues:
                if issue_path not in tasks:
                    report["warnings"].append(f"Version '{v_name}' lists issue '{issue_path}', but the task does not declare this version or file is missing.")

        if report["errors"] or report["agent_readiness_failures"]:
            report["status"] = "failed"

        self._write_reports(report)
        return report

    def _check_agent_readiness(self, content: str) -> List[str]:
        errors = []
        cfg = self.config.harness.agent_readiness
        
        if cfg.require_title and not re.search(r'^#\s+.+', content, re.MULTILINE):
            errors.append("Missing H1 Title (# Title)")
            
        if cfg.require_summary and not re.search(r'^##\s+(요약|Summary)', content, re.MULTILINE):
            errors.append("Missing Summary section (## 요약)")
            
        if cfg.require_decisions_section and not re.search(r'^##\s+(결정사항|Decisions)', content, re.MULTILINE):
            errors.append("Missing Decisions section (## 결정사항)")
            
        if cfg.require_todo_owner:
            todos = re.findall(r'^-\s+\[ \]\s+(.+)', content, re.MULTILINE)
            for todo in todos:
                if not re.match(r'^@\w+', todo):
                    errors.append(f"TODO missing owner (@username): {todo}")
                    
        if cfg.check_mermaid:
            # Check for unclosed mermaid blocks or syntax errors
            blocks = re.findall(r'```mermaid\n(.*?)(?:```|$)', content, re.DOTALL)
            for block in blocks:
                if not block.strip():
                    errors.append("Empty mermaid block")
                elif "graph " not in block and "sequenceDiagram" not in block and "flowchart " not in block and "gantt" not in block and "classDiagram" not in block and "stateDiagram" not in block and "pie" not in block and "erDiagram" not in block and "journey" not in block:
                    errors.append("Mermaid block missing valid diagram type declaration")
            if content.count("```mermaid") > content.count("```") / 2:
                errors.append("Unclosed mermaid block detected")

        return errors

    def _check_links(self, content: str, file_path: Path) -> List[str]:
        errors = []
        file_dir = file_path.parent
        for match in LINK_RE.finditer(content):
            link_text, target = match.group(1), match.group(2)
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            target_clean = target.split("#")[0]
            if not target_clean:
                continue
            if target_clean.startswith("/"):
                resolved = self.workspace_root / target_clean.lstrip("/")
            else:
                resolved = file_dir / target_clean
            if not resolved.resolve().exists():
                errors.append(f"Broken link: [{link_text}]({target}) -> {resolved}")
        return errors

    def _write_reports(self, report: Dict[str, Any]):
        # Write JSON
        json_path = self.reports_dir / "latest.json"
        json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        
        # Write Markdown
        md_path = self.reports_dir / "latest.md"
        md_lines = [
            f"# Harness Validation Report",
            f"**Status:** {report['status'].upper()}",
            f"**Files Checked:** {report['files_checked']}",
            ""
        ]
        
        if report["errors"]:
            md_lines.append("## Errors")
            for err in report["errors"]:
                md_lines.append(f"- ❌ {err}")
            md_lines.append("")
            
        if report["warnings"]:
            md_lines.append("## Warnings")
            for warn in report["warnings"]:
                md_lines.append(f"- ⚠️ {warn}")
            md_lines.append("")
            
        if report["agent_readiness_failures"]:
            md_lines.append("## Agent Readiness Failures")
            for fail in report["agent_readiness_failures"]:
                md_lines.append(f"### `{fail['file']}`")
                for err in fail["errors"]:
                    md_lines.append(f"- {err}")
            md_lines.append("")
            
        md_path.write_text("\n".join(md_lines), encoding="utf-8")

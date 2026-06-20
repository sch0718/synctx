"""Front Matter 파서."""

from __future__ import annotations

import re
from typing import Any, Dict, Tuple

import yaml
from pydantic import BaseModel, Field


_FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


class SynctxFrontMatter(BaseModel):
    type: str | None = None
    target: str | None = None
    subject: str | None = None
    status: str | None = None
    priority: str | None = None
    version: str | None = None
    due_date: str | None = None
    assignee: str | None = None
    name: str | None = None
    issues: list[str] = Field(default_factory=list)


class FrontMatter(BaseModel):
    title: str | None = None
    synctx: SynctxFrontMatter | None = None


def parse_frontmatter(content: str) -> Tuple[Dict[str, Any], str]:
    """마크다운 문자열에서 Front Matter를 파싱하고 본문과 분리하여 반환한다.
    
    Args:
        content: 마크다운 문자열
        
    Returns:
        (front_matter_dict, body_content)
    """
    match = _FRONT_MATTER_RE.match(content)
    if not match:
        return {}, content
        
    yaml_str = match.group(1)
    try:
        front_matter = yaml.safe_load(yaml_str)
        if not isinstance(front_matter, dict):
            front_matter = {}
    except yaml.YAMLError:
        front_matter = {}
        
    body_content = content[match.end():]
    return front_matter, body_content


def validate_frontmatter(front_matter: Dict[str, Any]) -> FrontMatter:
    return FrontMatter.model_validate(front_matter or {})

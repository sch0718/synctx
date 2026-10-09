"""Sync Engine용 synctx-html 블록 처리: 합본, 합본 반영 해시, XWiki·OpenProject 본문 변환."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Tuple

from synctx_common.config import HtmlEmbedConfig
from synctx_common.html_bundle import (
    BundleError,
    bundle,
    find_blocks,
    parse_block,
    render_notice,
    replace_blocks,
    to_iframe,
    xwiki_html_macro,
)

DISABLED_MESSAGE = "이 저장소는 HTML 실행이 허용되지 않습니다. Viewer에서 원본을 확인하세요."
ERROR_PREFIX = "인터랙티브 콘텐츠를 불러오지 못했습니다: "
OP_REPLACEMENT = "> 인터랙티브 콘텐츠: [Viewer에서 보기]({url})"


@dataclass(frozen=True)
class BlockOutcome:
    status: str
    fragment: str
    digest: str
    deps: Tuple[str, ...]
    message: str = ""


@dataclass(frozen=True)
class MarkdownAnalysis:
    outcomes: Tuple[BlockOutcome, ...]

    @property
    def deps(self) -> list:
        return sorted({dep for outcome in self.outcomes for dep in outcome.deps})


def policy_fingerprint(policy: HtmlEmbedConfig) -> str:
    return hashlib.sha256(json.dumps(policy.model_dump(), sort_keys=True).encode("utf-8")).hexdigest()


def _analyze_block(text: str, md_path: str, policy: HtmlEmbedConfig, read) -> BlockOutcome:
    try:
        block = parse_block(text, md_path)
    except BundleError as exc:
        return BlockOutcome(exc.reason, render_notice(ERROR_PREFIX + str(exc), None), f"error:{exc.reason}:{exc}", (), str(exc))
    if not policy.enabled:
        return BlockOutcome("disabled", render_notice(DISABLED_MESSAGE, block.src), "disabled", (block.src,))
    try:
        result = bundle(read, block.src, policy)
    except BundleError as exc:
        deps = tuple(sorted(set(exc.files) | set(exc.missing) | {block.src}))
        return BlockOutcome(exc.reason, render_notice(ERROR_PREFIX + str(exc), block.src), f"error:{exc.reason}:{exc}", deps, str(exc))
    return BlockOutcome("ok", to_iframe(result, block.aspect, block.height, block.title), result.sha256, tuple(result.files))


def analyze(body: str, md_path: str, policy: HtmlEmbedConfig, read) -> MarkdownAnalysis:
    return MarkdownAnalysis(tuple(_analyze_block(match.body, md_path, policy, read) for match in find_blocks(body)))


def effective_sha(content: str, analysis: MarkdownAnalysis, fingerprint: str) -> str:
    """블록이 없으면 기존과 같은 본문 SHA를 돌려줘, 기능 도입 후 모든 문서가 다시 게시되지 않게 한다."""
    if not analysis.outcomes:
        return hashlib.sha256(content.encode()).hexdigest()
    digest = hashlib.sha256(content.encode())
    for outcome in analysis.outcomes:
        digest.update(b"\0" + outcome.digest.encode())
        digest.update(b"\0" + "\n".join(outcome.deps).encode())
    digest.update(b"\0" + fingerprint.encode())
    return digest.hexdigest()


def render_for_xwiki(body: str, analysis: MarkdownAnalysis) -> str:
    return replace_blocks(body, lambda index, match: xwiki_html_macro(analysis.outcomes[index].fragment))


def render_for_openproject(body: str, viewer_url: str) -> str:
    return replace_blocks(body, lambda index, match: OP_REPLACEMENT.format(url=viewer_url))

"""Viewer용 synctx-html 블록 처리: Markdown 자리표시를 만들고 sanitizer 뒤에 iframe으로 채운다."""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field
from typing import Callable

from synctx_common.config import HtmlEmbedConfig
from synctx_common.html_bundle import BundleError, bundle, parse_block, render_notice, replace_blocks, to_iframe
from synctx_common.observability import html_bundle_total

DISABLED_MESSAGE = "이 저장소는 HTML 실행이 허용되지 않습니다."
ERROR_PREFIX = "인터랙티브 콘텐츠를 불러오지 못했습니다: "
CDN_NOTICE = "사내 CDN 접근이 필요할 수 있는 콘텐츠입니다. 네트워크가 없으면 일부가 표시되지 않을 수 있습니다."
PDF_REPLACEMENT = "> 인터랙티브 콘텐츠: [Viewer에서 보기]({url})"


@dataclass
class PreparedMarkdown:
    text: str
    blocks: list = field(default_factory=list)
    nonce: str = ""


def prepare(md_text: str) -> PreparedMarkdown:
    nonce = secrets.token_hex(8)
    blocks = []

    def slot(index, block):
        blocks.append(block.body)
        return f'\n\n<div class="synctx-html-slot" data-slot="{nonce}-{index}"></div>\n\n'

    return PreparedMarkdown(replace_blocks(md_text, slot), blocks, nonce)


def render_block(text, md_path, policy: HtmlEmbedConfig, read, source_href, download) -> tuple:
    try:
        block = parse_block(text, md_path)
    except BundleError as exc:
        return render_notice(ERROR_PREFIX + str(exc), None), exc.reason
    if not policy.enabled:
        return render_notice(DISABLED_MESSAGE, block.src, source_href(block.src)), "disabled"
    try:
        result = bundle(read, block.src, policy)
    except BundleError as exc:
        return render_notice(ERROR_PREFIX + str(exc), block.src, source_href(block.src)), exc.reason
    iframe = to_iframe(result, block.aspect, block.height, block.title)
    if download and policy.allowed_origins:
        iframe = render_notice(CDN_NOTICE, None) + iframe
    return iframe, "ok"


def fill(
    html: str,
    prepared: PreparedMarkdown,
    *,
    md_path: str,
    policy: HtmlEmbedConfig,
    read,
    repo_id: str,
    source_href: Callable[[str], str],
    download: bool = False,
) -> str:
    if not prepared.blocks:
        return html
    pattern = re.compile(rf'<div class="synctx-html-slot" data-slot="{prepared.nonce}-(\d+)"></div>')

    def replace(match):
        index = int(match.group(1))
        if index >= len(prepared.blocks):
            return ""
        fragment, result = render_block(prepared.blocks[index], md_path, policy, read, source_href, download)
        html_bundle_total.labels(service="viewer", repo_id=repo_id, result=result).inc()
        return fragment

    return pattern.sub(replace, html)


def strip_for_pdf(md_text: str, viewer_url: str) -> str:
    return replace_blocks(md_text, lambda index, block: PDF_REPLACEMENT.format(url=viewer_url))

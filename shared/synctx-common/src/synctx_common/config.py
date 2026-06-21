"""synctx 공통 설정 로더 및 Pydantic 모델."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import yaml
from pydantic import BaseModel, Field, field_validator


class AuthzRoleMappings(BaseModel):
    viewer: List[str] = Field(default_factory=list)
    publisher: List[str] = Field(default_factory=list)
    cdn_admin: List[str] = Field(default_factory=list)
    sync_admin: List[str] = Field(default_factory=list)
    feedback_admin: List[str] = Field(default_factory=list)
    platform_admin: List[str] = Field(default_factory=list)


class AuthzConfig(BaseModel):
    enabled: bool = True
    group_header: str = "X-Forwarded-Groups"
    role_mappings: AuthzRoleMappings = Field(default_factory=AuthzRoleMappings)


class RuntimeConfig(BaseModel):
    environment: Literal["production", "development", "test"] = "production"
    public_base_url: str = "http://localhost"
    request_id_header: str = "X-Request-Id"


class RepoAuthConfig(BaseModel):
    type: Literal["token", "ssh", "none"] = "none"
    credential_env: Optional[str] = None
    credential_file: Optional[str] = None
    ssh_key_file: Optional[str] = None


class RepoProfile(BaseModel):
    id: str
    name: str
    url: str
    default_ref: str = "main"
    auth: RepoAuthConfig = Field(default_factory=RepoAuthConfig)
    watch_paths: List[str] = Field(default_factory=list)
    ignore_paths: List[str] = Field(default_factory=list)
    storage_mode: Literal["full", "shallow", "blobless", "treeless"] = "full"
    shallow_depth: int = 0
    sparse_paths: List[str] = Field(default_factory=list)


class TargetMatchConfig(BaseModel):
    path_prefix: str
    extensions: List[str]


class XWikiTargetMapping(BaseModel):
    space_from: Literal["directory"] = "directory"
    page_from: Literal["filename"] = "filename"


class OpenProjectTargetMapping(BaseModel):
    project: str


class MappingRule(BaseModel):
    match: TargetMatchConfig
    target: Literal["xwiki", "openproject"]
    xwiki: Optional[XWikiTargetMapping] = None
    openproject: Optional[OpenProjectTargetMapping] = None


class TargetAuthConfig(BaseModel):
    type: Literal["basic", "token"]
    credential_env: str


class TargetRetryConfig(BaseModel):
    max_attempts: int = 3
    backoff_seconds: List[int] = Field(default_factory=lambda: [1, 5, 15])


class XWikiTargetConfig(BaseModel):
    base_url: str
    wiki: str = "xwiki"
    syntax: str = "markdown/1.2"
    auth: TargetAuthConfig
    concurrency: int = 5
    timeout_seconds: int = 30
    retry: TargetRetryConfig = Field(default_factory=TargetRetryConfig)


class OpenProjectTargetConfig(BaseModel):
    base_url: str
    auth: TargetAuthConfig
    external_key_custom_field: str = "synctx_external_key"
    protected_fields: List[str] = Field(default_factory=lambda: ["assignee"])
    type_map: Dict[str, int] = Field(default_factory=dict)
    status_map: Dict[str, int] = Field(default_factory=dict)
    priority_map: Dict[str, int] = Field(default_factory=dict)


class TargetsConfig(BaseModel):
    xwiki: Optional[XWikiTargetConfig] = None
    openproject: Optional[OpenProjectTargetConfig] = None


class ViewerConfig(BaseModel):
    repo_cache_path: str = "/data/repos"
    max_render_size_bytes: int = 5242880
    cdn_base_url: str = "/cdn"


class CdnUploadAuthConfig(BaseModel):
    enabled: bool = True
    required_role: str = "cdn-admin"


class CdnConfig(BaseModel):
    storage_path: str = "/data/cdn"
    upload_auth: CdnUploadAuthConfig = Field(default_factory=CdnUploadAuthConfig)
    max_upload_size_bytes: int = 50_000_000
    allowed_extensions: List[str] = Field(
        default_factory=lambda: [".js", ".css", ".woff", ".woff2", ".ttf", ".svg", ".png", ".map"]
    )


class MarpConfig(BaseModel):
    publish_repo: str = ""
    publish_branch: str = "main"
    publish_base_path: str = "_published/slides"
    worktree_path: str = "/data/marp/publish-worktree"
    conversion_timeout_seconds: int = 120
    max_input_size_bytes: int = 10485760


class SlidesConfig(BaseModel):
    worktree_path: str = "/data/slides/worktree"
    refresh_mode: Literal["webhook_and_polling", "polling", "webhook"] = "webhook_and_polling"
    polling_interval_seconds: int = 60


class SyncConfig(BaseModel):
    state_path: str = "/data/sync-state"
    lease_namespace: str = "synctx"
    schedule: str = "*/5 * * * *"
    on_delete: Literal["mark", "delete", "ignore"] = "mark"
    force_push_policy: Literal["full_rescan", "fail", "ignore"] = "full_rescan"
    dry_run: bool = False


class AgentReadinessConfig(BaseModel):
    require_title: bool = True
    require_summary: bool = True
    require_decisions_section: bool = True
    require_todo_owner: bool = True
    check_links: bool = True
    check_mermaid: bool = True


class HarnessConfig(BaseModel):
    context_path: str = ".context"
    reports_path: str = "reports/harness"
    required_context_files: List[str] = Field(
        default_factory=lambda: ["system_prompt.md", "project_state.yaml", "glossary.md", "decision-log.md"]
    )
    blueprint_path: str = ".context/blueprints"
    agent_readiness: AgentReadinessConfig = Field(default_factory=AgentReadinessConfig)


class OutboundMailConfig(BaseModel):
    smtp_host: str = "smtp-relay.internal"
    smtp_port: int = 587
    credential_env: str = "OUTBOUND_SMTP_CREDENTIAL"
    from_: str = Field("synctx-feedback@internal", alias="from")
    to: str = "synctx-feedback-import@example.com"


class ExternalImporterConfig(BaseModel):
    create_pr: bool = True
    direct_push: bool = False
    branch_prefix: str = "synctx-feedback/"


class OutboundFeedbackConfig(BaseModel):
    enabled: bool = False
    transport: Literal["email"] = "email"
    mode: Literal["pr_only"] = "pr_only"
    change_detection: Literal["polling", "webhook"] = "polling"
    polling_interval_seconds: int = 300
    allowed_targets: List[str] = Field(default_factory=lambda: ["openproject"])
    allowed_fields: List[str] = Field(default_factory=lambda: ["status", "priority", "version", "due_date"])
    include_subject: bool = False
    include_description: bool = False
    include_comments: bool = False
    include_attachments: bool = False
    user_identity: Literal["hashed", "clear"] = "hashed"
    require_hmac_signature: bool = True
    replay_window_minutes: int = 1440
    outbound_mail: OutboundMailConfig = Field(default_factory=OutboundMailConfig)
    external_importer: ExternalImporterConfig = Field(default_factory=ExternalImporterConfig)


class SynctxConfig(BaseModel):
    version: str = "1"
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    authz: AuthzConfig = Field(default_factory=AuthzConfig)
    repositories: List[RepoProfile] = Field(default_factory=list)
    mappings: List[MappingRule] = Field(default_factory=list)
    targets: TargetsConfig = Field(default_factory=TargetsConfig)
    viewer: ViewerConfig = Field(default_factory=ViewerConfig)
    cdn: CdnConfig = Field(default_factory=CdnConfig)
    marp: MarpConfig = Field(default_factory=MarpConfig)
    slides: SlidesConfig = Field(default_factory=SlidesConfig)
    sync: SyncConfig = Field(default_factory=SyncConfig)
    harness: HarnessConfig = Field(default_factory=HarnessConfig)
    outbound_feedback: OutboundFeedbackConfig = Field(default_factory=OutboundFeedbackConfig)


def load_config(config_path: str | Path | None = None) -> SynctxConfig:
    """YAML 설정 파일을 로드하여 검증된 Pydantic 모델로 반환한다."""
    if config_path is None:
        config_path = os.getenv("SYNCTX_CONFIG_PATH", ".synctx.yaml")
    
    path = Path(config_path)
    if not path.is_file():
        # 기본값으로 초기화된 설정 반환
        return SynctxConfig()
        
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
        
    if not data:
        return SynctxConfig()
        
    # 'synctx' 최상위 키가 있으면 제거 (version 확인용)
    if "synctx" in data and isinstance(data["synctx"], dict):
        version = str(data["synctx"].get("version", "1"))
        data["version"] = version
        del data["synctx"]
        
    return SynctxConfig.model_validate(data)

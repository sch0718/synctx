"""정적 내보내기 설정(static-export.yaml) 읽기와 검증."""
from __future__ import annotations

import importlib
import re
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

_REPO_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _reserved_paths() -> frozenset[str]:
    return frozenset(importlib.import_module("app").RESERVED_PATHS)


def _theme_names() -> frozenset[str]:
    return frozenset(importlib.import_module("renderer").THEME_CSS)


class SiteRepo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    url: str
    default_ref: str = "main"
    auth: dict = Field(default_factory=dict)
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        if not _REPO_ID.fullmatch(value):
            raise ValueError("저장소 id는 영문·숫자·`-`·`_`만 쓸 수 있습니다")
        if value in _reserved_paths():
            raise ValueError(f"저장소 id `{value}`는 Viewer 예약 경로와 같아 쓸 수 없습니다")
        return value

    @field_validator("url")
    @classmethod
    def _check_url(cls, value: str) -> str:
        if not value.strip() or any(ord(ch) < 32 for ch in value):
            raise ValueError("저장소 url이 비어 있거나 제어 문자를 포함합니다")
        if "://" in value:
            try:
                parsed = urlsplit(value)
                credentials = parsed.password is not None or (parsed.username is not None and parsed.scheme != "ssh")
            except ValueError:
                raise ValueError("저장소 url 형식이 올바르지 않습니다") from None
            if credentials:
                raise ValueError("저장소 url에 인증 정보를 넣을 수 없습니다. auth.credential_env를 쓰세요")
        return value

    @field_validator("auth")
    @classmethod
    def _check_auth(cls, value: dict) -> dict:
        extra = sorted(str(key) for key in value if key != "credential_env")
        if extra:
            raise ValueError(f"auth에는 credential_env만 쓸 수 있습니다(허용되지 않는 키: {', '.join(extra)})")
        env = value.get("credential_env")
        if "credential_env" in value and not (isinstance(env, str) and _ENV_NAME.fullmatch(env)):
            raise ValueError("auth.credential_env는 환경변수 이름이어야 합니다")
        return value


class SiteConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    theme: str = "corporate"
    default_repo: str | None = None
    repositories: list[SiteRepo]

    @field_validator("title")
    @classmethod
    def _check_title(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title이 비어 있습니다")
        return value

    @field_validator("theme")
    @classmethod
    def _check_theme(cls, value: str) -> str:
        if value not in _theme_names():
            raise ValueError(f"theme `{value}`은 지원하지 않습니다(지원: {', '.join(sorted(_theme_names()))})")
        return value

    @model_validator(mode="after")
    def _check_repositories(self) -> "SiteConfig":
        if not self.repositories:
            raise ValueError("repositories에 저장소가 하나 이상 있어야 합니다")
        ids = [repo.id for repo in self.repositories]
        duplicated = sorted({i for i in ids if ids.count(i) > 1})
        if duplicated:
            raise ValueError(f"저장소 id가 중복됩니다: {', '.join(duplicated)}")
        if self.default_repo is not None and self.default_repo not in ids:
            raise ValueError(f"default_repo `{self.default_repo}`가 repositories에 없습니다")
        return self


def load_site_config(path: Path, selected: list[str] | None = None) -> SiteConfig:
    """설정을 읽어 검증한다. selected가 있으면 설정 파일 순서를 유지한 채 그 저장소만 남긴다."""
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except OSError:
        raise ValueError(f"설정 파일을 읽을 수 없습니다: {path}") from None
    except yaml.YAMLError:
        raise ValueError(f"설정 파일이 올바른 YAML이 아닙니다: {path}") from None
    if not isinstance(data, dict):
        raise ValueError("설정 파일이 비어 있거나 매핑이 아닙니다")
    try:
        site = SiteConfig.model_validate(data)
    except ValidationError as exc:
        # pydantic 메시지는 입력값(URL의 인증 정보 등)을 담으므로 위치와 사유만 옮긴다.
        problems = "; ".join(f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in exc.errors())
        raise ValueError(f"설정이 올바르지 않습니다 — {problems}") from None
    if selected is None:
        return site
    known = {repo.id for repo in site.repositories}
    unknown = [repo_id for repo_id in selected if repo_id not in known]
    if unknown:
        raise ValueError(f"설정에 없는 저장소 id입니다: {', '.join(unknown)}")
    wanted = set(selected)
    repositories = [repo for repo in site.repositories if repo.id in wanted]
    if not repositories:
        raise ValueError("선택한 저장소가 없습니다")
    default_repo = site.default_repo if site.default_repo in wanted else None
    return site.model_copy(update={"repositories": repositories, "default_repo": default_repo})

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path


class SecretKeyError(ValueError):
    pass


def load_secret_key(name: str, environ: Mapping[str, str] | None = None) -> bytes | None:
    env = os.environ if environ is None else environ
    file_name = name + "_FILE"
    if name in env and file_name in env:
        raise SecretKeyError(f"{name}와 {file_name}은 함께 지정할 수 없습니다")
    if file_name in env:
        try:
            value = Path(env[file_name]).read_bytes().decode("utf-8")
        except (OSError, UnicodeError, ValueError) as exc:
            raise SecretKeyError(f"{file_name} 파일을 읽을 수 없습니다") from exc
        if value.endswith("\r\n"):
            value = value[:-2]
        elif value.endswith("\n"):
            value = value[:-1]
    elif name in env:
        value = env[name]
    else:
        return None
    try:
        key = value.encode("utf-8")
    except UnicodeError as exc:
        raise SecretKeyError(f"{name}은 UTF-8이어야 합니다") from exc
    if len(key) < 32:
        raise SecretKeyError(f"{name}은 UTF-8로 32바이트 이상이어야 합니다")
    return key


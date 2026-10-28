"""Compose Jitsi 설정과 키를 출력 없이 검사한다."""

import ipaddress
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from synctx_common.config import JitsiConfig
from synctx_common.secret_keys import load_secret_key


def validate(path):
    config = yaml.safe_load(Path(path).read_text()) or {}
    jitsi = JitsiConfig.model_validate(config.get("collab", {}).get("jitsi", {})).model_dump()
    if not jitsi.get("enabled"):
        raise ValueError("collab.jitsi.enabled=true가 필요합니다")
    if jitsi.get("public_url") != os.environ["SYNCTX_JITSI_PUBLIC_URL"] or jitsi.get("app_id", "synctx") != os.environ.get("SYNCTX_JITSI_APP_ID", "synctx"):
        raise ValueError("Compose와 collab.jitsi의 public_url/app_id가 다릅니다")
    if (jitsi.get("xmpp_domain") or urlsplit(jitsi["public_url"]).hostname) != os.environ["SYNCTX_JITSI_HOST"]:
        raise ValueError("내장 Jitsi XMPP 도메인은 public 호스트와 일치해야 합니다")
    for address in os.environ["SYNCTX_JITSI_ADVERTISE_IPS"].split(","):
        ipaddress.ip_address(address)
    for name in ("JITSI_JICOFO_PASSWORD", "JITSI_JVB_PASSWORD"):
        if len(os.environ.get(name, "").encode("utf-8")) < 32:
            raise ValueError("Jitsi XMPP 암호는 32바이트 이상이어야 합니다")
    keys = []
    for field, name in (("secret_env", "JITSI_APP_SECRET"), ("room_secret_env", "JITSI_ROOM_SECRET")):
        if jitsi.get(field, name) != name:
            raise ValueError("Compose에서는 Jitsi 기본 환경변수 이름을 사용하세요")
        env = dict(os.environ)
        for suffix in ("", "_PREVIOUS"):
            slot = name + suffix
            mount = env.get(slot + "_FILE_MOUNT", "/dev/null")
            if mount != "/dev/null":
                env[slot + "_FILE"] = mount
            key = load_secret_key(slot, env)
            if not suffix:
                if not key:
                    raise ValueError("Jitsi 현재 키 파일 또는 환경변수가 필요합니다")
                keys.append(key)
    if keys[0] == keys[1]:
        raise ValueError("Jitsi 앱 키와 방 이름 키는 서로 달라야 합니다")


if __name__ == "__main__":
    try:
        validate(sys.argv[1])
    except (OSError, ValueError, KeyError, yaml.YAMLError):
        print("Jitsi 설정·키 파일을 확인하세요(public_url/app_id, 기본 환경변수 이름, 서로 다른 32바이트 이상 현재 키).", file=sys.stderr)
        sys.exit(1)

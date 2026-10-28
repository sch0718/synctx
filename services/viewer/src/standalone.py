"""Run the existing Markdown/meeting viewer with its bundled assets only."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager, suppress
import hashlib
import importlib.util
import ipaddress
import logging
import math
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import urlsplit

import yaml

logger = logging.getLogger(__name__)
DEFAULT_CACHE = Path.home() / ".cache" / "synctx" / "lite"
# Prefix every derived id, so repository names cannot shadow Viewer routes.
REPO_PREFIX = "repo-"


def _repo(value: str) -> str:
    """Accept Git transports and local paths, never credentials embedded in a URL."""
    if not value.strip() or any(ord(ch) < 32 for ch in value):
        raise argparse.ArgumentTypeError("repository must be a Git URL or local path")
    if "://" in value:
        try:
            parsed = urlsplit(value)
            if parsed.scheme not in {"http", "https", "ssh", "git", "file"}:
                raise ValueError
            if parsed.query or parsed.fragment or parsed.password or (parsed.username and parsed.scheme != "ssh"):
                raise ValueError
            if parsed.scheme != "file" and not parsed.hostname:
                raise ValueError
            if parsed.port is not None and not 1 <= parsed.port <= 65535:
                raise ValueError
        except ValueError:
            raise argparse.ArgumentTypeError("invalid repository URL; use environment credentials, never URL credentials") from None
    return value


def _host(value: str) -> str:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        if len(value) > 253 or not all(re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", part) for part in value.split(".")):
            raise argparse.ArgumentTypeError("host must be an IP address or hostname") from None
    return value


def _public_base_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or any(ch.isspace() for ch in value)):
            raise ValueError
        _host(parsed.hostname)
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError
    except (ValueError, argparse.ArgumentTypeError):
        raise argparse.ArgumentTypeError("public base URL must be HTTP(S), without credentials, query or fragment") from None
    return value


def _port(value: str) -> int:
    try:
        port = int(value)
        if 1 <= port <= 65535:
            return port
    except ValueError:
        pass
    raise argparse.ArgumentTypeError("port must be between 1 and 65535")


def _interval(value: str) -> float:
    try:
        interval = float(value)
        if math.isfinite(interval) and interval >= 0:
            return interval
    except ValueError:
        pass
    raise argparse.ArgumentTypeError("refresh interval must be finite and nonnegative")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the standalone synctx Markdown and meeting viewer")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--repo", action="append", type=_repo, help="Git URL or local path (repeatable)")
    source.add_argument("--config", type=Path, help="existing .synctx.yaml")
    parser.add_argument("--ref", help="Git branch/ref for --repo (default main)")
    parser.add_argument("--host", type=_host, default="127.0.0.1")
    parser.add_argument("--port", type=_port, default=18000)
    parser.add_argument("--public-base-url", type=_public_base_url, help="public HTTP(S) URL for exported source links")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--refresh-interval", type=_interval, default=60)
    args = parser.parse_args(argv)
    if args.config:
        if args.ref is not None:
            parser.error("--ref applies only to --repo; set default_ref in the --config file")
        return args
    if args.ref is None:
        args.ref = "main"
    if not args.ref.strip() or args.ref.startswith("-") or any(ch.isspace() for ch in args.ref):
        parser.error("ref must be a Git branch or ref name")
    return args


def prepare_config(args: argparse.Namespace) -> Path:
    # Import configuration only here; importing the Viewer would read the environment too early.
    from synctx_common.config import SynctxConfig, load_config

    if args.config:
        if not args.config.is_file():
            raise SystemExit("configuration file does not exist")
        data = load_config(args.config).model_dump(exclude_unset=True)
        if not data.get("repositories"):
            raise SystemExit("configuration must contain at least one repository")
        source = yaml.safe_load(args.config.read_text())
        source_viewer = source.get("viewer") or {}
        if "render_token_ttl_seconds" in source_viewer:
            data.setdefault("viewer", {})["render_token_ttl_seconds"] = source_viewer["render_token_ttl_seconds"]
        for repo, original in zip(data["repositories"], source["repositories"]):
            embed = original.get("html_embed") or {}
            if "render_files" in embed:
                repo.setdefault("html_embed", {})["render_files"] = embed["render_files"]
        for repo in data["repositories"]:
            try:
                _repo(repo["url"])
            except argparse.ArgumentTypeError as exc:
                raise SystemExit(str(exc)) from None
    else:
        repos = []
        used = set()
        for url in args.repo:
            # Canonicalize before hashing: caller-relative paths can have different origins.
            if "://" not in url and ":" not in url:
                url = str(Path(url).resolve())
            path = urlsplit(url).path if "://" in url else url.split(":", 1)[-1]
            name = Path(path.rstrip("/")).name.removesuffix(".git") or "repository"
            base = REPO_PREFIX + (re.sub(r"[^a-zA-Z0-9_-]+", "-", name).strip("-").lower() or "repository")
            base += "-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:8]
            repo_id = base
            suffix = 2
            while repo_id in used:
                repo_id = f"{base}-{suffix}"
                suffix += 1
            used.add(repo_id)
            auth = {"type": "token", "credential_env": "GIT_READ_TOKEN"} if url.startswith(("https://", "http://")) else {"type": "none"}
            repos.append({"id": repo_id, "name": name, "url": url, "default_ref": args.ref, "auth": auth, "collab": {"enabled": True}})
        data = {"authz": {"enabled": False}, "repositories": repos}

    cache = args.cache_dir.expanduser().resolve()
    cache.mkdir(parents=True, exist_ok=True)
    runtime = data.setdefault("runtime", {})
    if args.public_base_url is not None:
        runtime["public_base_url"] = args.public_base_url
    elif "public_base_url" not in runtime:
        public_env = os.getenv("SYNCTX_VIEWER_PUBLIC_BASE_URL")
        if public_env:
            try:
                runtime["public_base_url"] = _public_base_url(public_env)
            except argparse.ArgumentTypeError as exc:
                raise SystemExit(str(exc)) from None
        else:
            host = {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(args.host, args.host)
            host = f"[{host}]" if ":" in host else host
            runtime["public_base_url"] = f"http://{host}:{args.port}"
    data.setdefault("viewer", {}).update(asset_source="bundled", repo_cache_path=str(cache / "repos"))
    data.setdefault("collab", {}).setdefault("jitsi", {})["enabled"] = False
    data["viewer"].setdefault("render_token_ttl_seconds", 14400)
    SynctxConfig.model_validate(data)
    serialized = yaml.safe_dump(data, allow_unicode=True, sort_keys=True)
    fingerprint = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    target = cache / f"synctx.lite.{fingerprint}.yaml"
    if args.config and target == args.config.resolve():
        target = cache / f"synctx.lite.{fingerprint}.runtime.yaml"
    # Private, atomic writes avoid leaving a partial configuration after interruption.
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=cache, delete=False) as output:
        temporary = Path(output.name)
        try:
            output.write(serialized)
            output.close()
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    return target


def _load_viewer():
    source = Path(__file__).resolve().parent
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))
    spec = importlib.util.spec_from_file_location("synctx_standalone_viewer_app", source / "app.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


async def refresh_repositories(viewer, interval: float) -> None:
    while True:
        await asyncio.sleep(interval)
        startup = getattr(viewer, "_startup_sync", None)
        if startup is not None:
            await asyncio.shield(startup)
        for repo in viewer.config.repositories:
            try:
                await viewer.get_repo_manager(repo.id).fetch(source="standalone")
            except Exception as exc:
                # Exception strings can contain URLs/credentials from third-party transports.
                logger.warning("standalone_refresh_failed repo_id=%s error=%s", repo.id, type(exc).__name__)


def create_app(refresh_interval: float = 60, *, viewer=None):
    from starlette.applications import Starlette
    from starlette.responses import RedirectResponse
    from starlette.routing import Mount, Route

    viewer = viewer if viewer is not None else _load_viewer()

    @asynccontextmanager
    async def lifespan(application):
        async with viewer.app.router.lifespan_context(viewer.app):
            task = asyncio.create_task(refresh_repositories(viewer, refresh_interval)) if refresh_interval else None
            try:
                yield
            finally:
                if task is not None:
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task

    return Starlette(routes=[Route("/", lambda request: RedirectResponse("/view/")),
                             Mount("/view", app=viewer.app)], lifespan=lifespan)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    config_path = prepare_config(args)
    os.environ["SYNCTX_CONFIG_PATH"] = str(config_path)
    os.environ["SYNCTX_ENABLED_SERVICES"] = "viewer"
    import uvicorn
    uvicorn.run(create_app(args.refresh_interval), host=args.host, port=args.port, log_level="info", log_config=None)


if __name__ == "__main__":
    main()

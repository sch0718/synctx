"""Unique console entrypoint for the Sync service."""

from __future__ import annotations

import argparse
import asyncio
import logging

from synctx_common.config import load_config
from synctx_common.logging import configure_logging

from sync_engine import SyncEngine


configure_logging(service_name="sync")
logger = logging.getLogger(__name__)


async def async_main() -> int:
    parser = argparse.ArgumentParser(description="Run synctx sync engine")
    parser.add_argument("--config", default=None, help="Path to .synctx.yaml")
    parser.add_argument("--repo", default=None, help="Run only one repository id")
    parser.add_argument("--dry-run", action="store_true", help="Do not write targets or state")
    args = parser.parse_args()

    config = load_config(args.config)
    repos = [repo for repo in config.repositories if args.repo in (None, repo.id)]
    if not repos:
        logger.error("No repositories configured for sync")
        return 1

    engine = SyncEngine(config)
    failures = 0
    try:
        for repo in repos:
            try:
                report = await engine.run_sync(repo, dry_run=args.dry_run or config.sync.dry_run)
                if report and report.fail_count > 0:
                    failures += 1
                    logger.error(
                        "Sync partial failure for repo_id=%s: %s",
                        repo.id, report.summary(),
                    )
            except Exception:
                failures += 1
                logger.exception("Sync failed for repo_id=%s", repo.id)
    finally:
        await engine.close()

    return 1 if failures else 0


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()

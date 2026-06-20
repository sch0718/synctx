"""Unique console entrypoint for the Feedback service."""

import asyncio
import logging

from synctx_common.config import load_config

from exporter import FeedbackExporter


logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


async def async_main():
    config = load_config()
    exporter = FeedbackExporter(config)
    await exporter.run()


def main():
    logger.info("Starting Outbound Feedback Exporter...")
    asyncio.run(async_main())


if __name__ == "__main__":
    main()

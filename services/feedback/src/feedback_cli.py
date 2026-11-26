"""Unique console entrypoint for the Feedback service."""

import asyncio
import logging

from synctx_common.config import load_config
from synctx_common.logging import configure_logging

from exporter import FeedbackExporter


configure_logging(service_name="feedback")
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

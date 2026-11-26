import sys
import argparse
import logging
from pathlib import Path

from .config import load_config
from .harness import HarnessValidator
from .logging import configure_logging

configure_logging(service_name="synctx")
logger = logging.getLogger(__name__)

def main():
    parser = argparse.ArgumentParser(description="synctx CLI")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # validate-harness
    parser_harness = subparsers.add_parser("validate-harness", help="Validate harness engineering constraints")
    parser_harness.add_argument("--workspace", type=str, default=".", help="Workspace root directory")
    parser_harness.add_argument("--config", type=str, default=None, help="Path to .synctx.yaml")
    
    args = parser.parse_args()
    
    if args.command == "validate-harness":
        config = load_config(args.config)
        validator = HarnessValidator(config, args.workspace)
        report = validator.validate()
        
        if report["status"] == "failed":
            logger.error("Harness validation failed. See reports/harness/latest.md for details.")
            sys.exit(1)
        else:
            logger.info("Harness validation passed.")
            sys.exit(0)
    else:
        parser.print_help()
        sys.exit(1)

if __name__ == "__main__":
    main()

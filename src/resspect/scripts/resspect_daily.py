"""Command-line entry point for the daily FASTDB analysis."""

from __future__ import annotations

import argparse
import datetime
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Optional

from resspect.daily import run_daily
from resspect.daily_configuration import configure_logging, connect_to_fastdb, load_configuration


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the daily-analysis command."""
    parser = argparse.ArgumentParser(description="Run one daily RESSPECT analysis against FASTDB.")
    parser.add_argument("--config", required=True, type=Path, help="Path to the daily-analysis YAML file.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Perform the analysis without submitting spectrum requests to FASTDB.",
    )
    args = parser.parse_args(argv)
    config = load_configuration(args.config)

    run_id = datetime.datetime.now(tz=datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    log_path = configure_logging(config, run_id)
    logging.getLogger(__name__).info("Run log: %s", log_path)
    logging.getLogger(__name__).warning(
        "%s: spectrum requests %s be submitted",
        "DRY RUN" if args.dry_run else "LIVE RUN",
        "will not" if args.dry_run else "may",
    )

    fastdb = connect_to_fastdb(config)
    run_daily(config, fastdb, dry_run=args.dry_run, run_id=run_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import logging
import sys

from dotenv import load_dotenv

from config import Settings
from sweeper import BinanceSweeper


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sweep unexpected airdrop tokens into BNB/USDT.")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single sweep and exit (no polling loop).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Python logging level (DEBUG, INFO, WARNING...).",
    )
    return parser.parse_args()


def main() -> int:
    load_dotenv()
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    settings = Settings()
    sweeper = BinanceSweeper(settings)
    if args.once:
        sweeper.sweep_once()
        return 0

    sweeper.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())

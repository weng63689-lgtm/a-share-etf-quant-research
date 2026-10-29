"""Command-line interface for the offline paper-trading state machine."""

import argparse
import json

from a_share_quant.config import get_settings
from a_share_quant.paper.pipeline import PaperTradingPipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the offline ETF paper account")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Initialize paper-trading state")
    init.add_argument("--start-date", required=True)
    run = commands.add_parser("run", help="Process paper trading through an as-of date")
    run.add_argument("--as-of", required=True)
    commands.add_parser("run-latest", help="Process through the latest completed session")
    commands.add_parser("status", help="Show current paper-trading state")
    args = parser.parse_args()

    pipeline = PaperTradingPipeline(get_settings().project_root)
    if args.command == "init":
        result = pipeline.initialize(start_date=args.start_date)
    elif args.command == "run":
        result = pipeline.run(as_of=args.as_of)
    elif args.command == "run-latest":
        result = pipeline.run_latest()
    else:
        result = pipeline.status()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if result.get("status") not in {"ready", "active", "already_processed"}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

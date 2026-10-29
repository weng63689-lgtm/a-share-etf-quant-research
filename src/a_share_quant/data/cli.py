"""Command-line interface for reference data updates."""

import argparse
import json
from datetime import date

from a_share_quant.config import get_settings
from a_share_quant.data.history_pipeline import HistoryPipeline
from a_share_quant.data.pipeline import DataPipeline


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage A-share ETF reference data")
    subcommands = parser.add_subparsers(dest="command", required=True)
    update = subcommands.add_parser("update-all", help="Fetch, normalize, validate, and store data")
    update.add_argument("--calendar-start", default="2005-01-01")
    subcommands.add_parser("validate", help="Validate the latest stored data")
    subcommands.add_parser("summary", help="Print a compact data summary")
    update_history = subcommands.add_parser(
        "update-history",
        help="Fetch candidate ETF daily history and publish only if quality checks pass",
    )
    update_history.add_argument("--start-date")
    update_history.add_argument("--end-date")
    update_history.add_argument("--as-of")
    validate_history = subcommands.add_parser(
        "validate-history",
        help="Validate stored candidate ETF daily history",
    )
    validate_history.add_argument("--as-of")
    subcommands.add_parser("history-summary", help="Summarize stored ETF daily history")
    return parser


def main() -> None:
    args = _parser().parse_args()
    project_root = get_settings().project_root

    if args.command == "update-all":
        pipeline = DataPipeline(project_root)
        result = pipeline.update_all(calendar_start=args.calendar_start)
    elif args.command == "validate":
        pipeline = DataPipeline(project_root)
        errors = pipeline.validate()
        result = {"status": "ok" if not errors else "failed", "errors": errors}
        if errors:
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
            raise SystemExit(1)
    elif args.command == "summary":
        pipeline = DataPipeline(project_root)
        result = pipeline.summary()
    else:
        pipeline = HistoryPipeline(project_root)
        if args.command == "update-history":
            result = pipeline.update_history(
                start_date=_optional_date(args.start_date),
                end_date=_optional_date(args.end_date),
                as_of=_optional_date(args.as_of),
            )
        elif args.command == "validate-history":
            result = pipeline.validate_history(as_of=_optional_date(args.as_of))
            if result["status"] != "ok":
                print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
                raise SystemExit(1)
        else:
            result = pipeline.summary()

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


def _optional_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


if __name__ == "__main__":
    main()


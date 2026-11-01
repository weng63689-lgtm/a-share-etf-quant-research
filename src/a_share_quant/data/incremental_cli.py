"""Command-line interface for daily incremental ETF data updates."""

import argparse
import json
from datetime import date

from a_share_quant.config import get_settings
from a_share_quant.data.incremental import DailyDataUpdater


def main() -> None:
    parser = argparse.ArgumentParser(description="Update all frozen-universe ETF data")
    parser.add_argument("command", choices=["run", "cross-check"])
    parser.add_argument("--as-of")
    parser.add_argument("--days", type=int, default=10)
    args = parser.parse_args()
    as_of = date.fromisoformat(args.as_of) if args.as_of else None
    updater = DailyDataUpdater(get_settings().project_root)
    result = (
        updater.crosscheck_prices(days=args.days, as_of=as_of)
        if args.command == "cross-check"
        else updater.update(as_of=as_of)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if result.get("status") not in {"ok", "already_current"}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

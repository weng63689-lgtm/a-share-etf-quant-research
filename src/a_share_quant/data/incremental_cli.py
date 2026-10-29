"""Command-line interface for daily incremental ETF data updates."""

import argparse
import json
from datetime import date

from a_share_quant.config import get_settings
from a_share_quant.data.incremental import DailyDataUpdater


def main() -> None:
    parser = argparse.ArgumentParser(description="Update all frozen-universe ETF data")
    parser.add_argument("command", choices=["run"])
    parser.add_argument("--as-of")
    args = parser.parse_args()
    as_of = date.fromisoformat(args.as_of) if args.as_of else None
    result = DailyDataUpdater(get_settings().project_root).update(as_of=as_of)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if result.get("status") not in {"ok", "already_current"}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

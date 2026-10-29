"""Command-line interface for expanded-universe risk-budget research."""

import argparse
import json

from a_share_quant.backtest.risk_budget import RiskBudgetPipeline
from a_share_quant.config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Run ETF risk-budget comparisons")
    parser.add_argument("command", choices=["run", "summary"])
    args = parser.parse_args()
    pipeline = RiskBudgetPipeline(get_settings().project_root)
    result = pipeline.run() if args.command == "run" else pipeline.summary()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if result.get("status") != "ok":
        raise SystemExit(1)


if __name__ == "__main__":
    main()

"""Command-line interface for domestic multi-asset ETF risk budgeting."""

import argparse
import json

from a_share_quant.backtest.risk_budget import RiskBudgetPipeline
from a_share_quant.config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Run domestic multi-asset ETF risk budgets")
    parser.add_argument("command", choices=["run", "summary"])
    args = parser.parse_args()
    pipeline = RiskBudgetPipeline(
        get_settings().project_root,
        daily_dataset="multi_asset_research_etf_daily",
        universe_file="research_universe_v3.csv",
        output_prefix="multi_asset_",
        report_name="risk_budget_multi_asset",
    )
    result = pipeline.run() if args.command == "run" else pipeline.summary()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if result.get("status") != "ok":
        raise SystemExit(1)


if __name__ == "__main__":
    main()

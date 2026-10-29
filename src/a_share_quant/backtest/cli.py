"""Command-line interface for benchmark ingestion and strategy backtests."""

import argparse
import json

from a_share_quant.backtest.pipeline import BacktestPipeline
from a_share_quant.config import get_settings
from a_share_quant.data.benchmarks import BenchmarkPipeline
from a_share_quant.data.storage import DataStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the first ETF rotation backtest")
    parser.add_argument("command", choices=["update-benchmark", "run", "summary"])
    args = parser.parse_args()
    project_root = get_settings().project_root
    if args.command == "update-benchmark":
        result = BenchmarkPipeline(project_root).update()
    elif args.command == "run":
        result = BacktestPipeline(project_root).run()
    else:
        path = DataStore(project_root).processed_dir / "backtest_summary.json"
        if not path.is_file():
            raise FileNotFoundError("Run 'uv run quant-backtest run' first")
        result = json.loads(path.read_text(encoding="utf-8"))

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if result.get("status") != "ok":
        raise SystemExit(1)


if __name__ == "__main__":
    main()

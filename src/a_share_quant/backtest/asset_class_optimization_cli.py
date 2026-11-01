"""CLI for pre-registered domestic ETF asset-allocation optimization."""

import argparse
import json
from pathlib import Path

from a_share_quant.backtest.asset_class_optimization import (
    AssetClassOptimizationPipeline,
)
from a_share_quant.config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run pre-registered domestic ETF optimization experiments"
    )
    parser.add_argument("command", choices=["run", "summary"])
    args = parser.parse_args()
    project_root = get_settings().project_root
    pipeline = AssetClassOptimizationPipeline(project_root)
    if args.command == "run":
        result = pipeline.run()
    else:
        path = (
            Path(project_root)
            / "data"
            / "processed"
            / "asset_class_optimization_summary.json"
        )
        if not path.is_file():
            raise FileNotFoundError("Run 'uv run quant-optimize run' first")
        result = json.loads(path.read_text(encoding="utf-8"))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if result.get("status") != "ok":
        raise SystemExit(1)


if __name__ == "__main__":
    main()

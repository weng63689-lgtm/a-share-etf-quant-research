"""Command-line interface for ETF factor efficacy research."""

import argparse
import json

from a_share_quant.config import get_settings
from a_share_quant.strategy.factor_research import FactorResearchPipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze ETF cross-sectional factors")
    parser.add_argument("command", choices=["run", "summary"])
    args = parser.parse_args()
    pipeline = FactorResearchPipeline(get_settings().project_root)
    result = pipeline.run() if args.command == "run" else pipeline.summary()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if result.get("status") != "ok":
        raise SystemExit(1)


if __name__ == "__main__":
    main()

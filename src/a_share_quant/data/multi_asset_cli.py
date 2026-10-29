"""Command-line interface for the domestic multi-asset ETF research universe."""

import argparse
import json

from a_share_quant.config import get_settings
from a_share_quant.data.expanded_universe import ExpandedUniversePipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="Build domestic multi-asset ETF data")
    parser.add_argument("command", choices=["build"])
    parser.parse_args()
    project_root = get_settings().project_root
    result = ExpandedUniversePipeline(
        project_root,
        universe_file="research_universe_v3.csv",
        output_prefix="multi_asset_",
        base_daily_dataset="research_etf_daily",
        base_action_dataset="research_etf_corporate_actions",
    ).build()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if result.get("status") != "ok":
        raise SystemExit(1)


if __name__ == "__main__":
    main()

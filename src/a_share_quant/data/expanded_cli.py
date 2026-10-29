"""Command-line interface for the expanded ETF research universe."""

import argparse
import json

from a_share_quant.config import get_settings
from a_share_quant.data.expanded_universe import ExpandedUniversePipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="Build expanded domestic equity ETF data")
    parser.add_argument("command", choices=["build"])
    parser.parse_args()
    result = ExpandedUniversePipeline(get_settings().project_root).build()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if result.get("status") != "ok":
        raise SystemExit(1)


if __name__ == "__main__":
    main()

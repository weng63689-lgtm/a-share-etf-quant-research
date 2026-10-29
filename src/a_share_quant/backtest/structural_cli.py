"""Command-line interface for development-only structural candidate validation."""

import argparse
import json

from a_share_quant.backtest.structural_candidate import StructuralCandidatePipeline
from a_share_quant.config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a structural strategy candidate")
    parser.add_argument("command", choices=["run", "summary"])
    args = parser.parse_args()
    pipeline = StructuralCandidatePipeline(get_settings().project_root)
    result = pipeline.run() if args.command == "run" else pipeline.summary()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if result.get("status") != "ok":
        raise SystemExit(1)


if __name__ == "__main__":
    main()

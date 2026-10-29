"""Command-line interface for frozen strategy forward validation."""

import argparse
import json

from a_share_quant.backtest.forward_validation import ForwardValidationPipeline
from a_share_quant.config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate the frozen ETF strategy")
    parser.add_argument("command", choices=["run", "summary"])
    args = parser.parse_args()
    pipeline = ForwardValidationPipeline(get_settings().project_root)
    result = pipeline.run() if args.command == "run" else pipeline.summary()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if result.get("status") != "ok":
        raise SystemExit(1)


if __name__ == "__main__":
    main()

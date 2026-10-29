"""Command-line interface for transaction-cost stress scenarios."""

import argparse
import json

from a_share_quant.config import get_settings
from a_share_quant.execution.cost_pipeline import CostAuditPipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit ETF transaction costs")
    parser.add_argument("command", choices=["audit", "summary"])
    args = parser.parse_args()
    pipeline = CostAuditPipeline(get_settings().project_root)
    result = pipeline.audit() if args.command == "audit" else pipeline.summary()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if result.get("status") != "ok":
        raise SystemExit(1)


if __name__ == "__main__":
    main()

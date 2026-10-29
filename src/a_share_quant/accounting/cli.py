"""Command-line interface for corporate actions and buy-and-hold checks."""

import argparse
import json

from a_share_quant.accounting.baseline import AccountModelPipeline
from a_share_quant.config import get_settings
from a_share_quant.data.storage import DataStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the ETF account model")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("build", help="Refresh actions and run buy-and-hold checks")
    commands.add_parser("summary", help="Print the latest account model summary")
    commands.add_parser("validate", help="Validate the latest account model quality report")
    return parser


def main() -> None:
    args = _parser().parse_args()
    project_root = get_settings().project_root
    pipeline = AccountModelPipeline(project_root)
    if args.command == "build":
        result = pipeline.run()
    elif args.command == "validate":
        result = pipeline.validate()
    else:
        path = DataStore(project_root).processed_dir / "account_model_quality_report.json"
        if not path.is_file():
            raise FileNotFoundError("Run 'uv run quant-account build' first")
        summary_path = path.with_name("buy_hold_summary.json")
        result = json.loads(summary_path.read_text(encoding="utf-8"))

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if result.get("status") != "ok":
        raise SystemExit(1)


if __name__ == "__main__":
    main()

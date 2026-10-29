"""Frozen candidate validation on the final sample and rolling windows."""

import hashlib
import json
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd

from a_share_quant.accounting.config import load_account_config
from a_share_quant.backtest.engine import StrategyBacktestEngine
from a_share_quant.data.storage import DataStore
from a_share_quant.execution.costs import load_cost_model
from a_share_quant.execution.rules import load_trading_rules
from a_share_quant.strategy.config import load_strategy_config
from a_share_quant.strategy.momentum import build_signals


class ForwardValidationPipeline:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.store = DataStore(project_root)
        self.config_path = project_root / "configs" / "frozen" / "multi_asset_v1.yaml"

    def run(self) -> dict[str, object]:
        frozen_hash = hashlib.sha256(self.config_path.read_bytes()).hexdigest()
        algorithm_fingerprint = self._algorithm_fingerprint()
        manifest_path = self.store.processed_dir / "frozen_multi_asset_v1_manifest.json"
        if manifest_path.exists():
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
            if previous.get("config_sha256") != frozen_hash:
                raise ValueError(
                    "Frozen multi-asset configuration changed after validation started"
                )
            if previous.get("algorithm_sha256") != algorithm_fingerprint:
                raise ValueError(
                    "Frozen strategy implementation changed after validation started"
                )
        else:
            self.store._atomic_json(
                manifest_path,
                {
                    "config_sha256": frozen_hash,
                    "algorithm_sha256": algorithm_fingerprint,
                    "frozen_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
                    "config_path": str(self.config_path),
                },
            )

        daily = self.store.load_processed("multi_asset_research_etf_daily")
        actions = self.store.load_processed("multi_asset_research_etf_corporate_actions")
        calendar = self.store.load_processed("trading_calendar")
        benchmark = self.store.load_processed("benchmark_daily")
        universe = self.store.load_universe("research_universe_v3.csv")
        strategy_config = load_strategy_config(
            self.project_root,
            override_file=self.config_path,
        )
        account_config = load_account_config(self.project_root)
        cost_model = load_cost_model(self.project_root)
        rule_book = load_trading_rules(
            self.project_root,
            universe_symbols=set(universe["symbol"].astype(str).str.zfill(6)),
        )
        bundle = build_signals(
            daily,
            calendar,
            strategy_config,
            benchmark=benchmark,
        )
        engine = StrategyBacktestEngine(
            self.project_root,
            strategy_config=strategy_config,
            account_config=account_config,
        )
        final_start = pd.Timestamp("2023-10-30")
        final_end = pd.Timestamp("2026-10-29")
        final_rows: list[dict[str, object]] = []
        snapshots_frames: list[pd.DataFrame] = []
        orders_frames: list[pd.DataFrame] = []
        for stress in (Decimal("1"), Decimal("2"), Decimal("3")):
            result = engine.run(
                daily=daily,
                actions=actions,
                calendar=calendar,
                benchmark=benchmark,
                signal_bundle=bundle,
                rule_book=rule_book,
                cost_model=cost_model,
                scenario=f"final_oos_cost_{stress}x",
                stress_multiplier=stress,
                with_costs=True,
                start_date=final_start,
                end_date=final_end,
            )
            row = {
                "scenario": f"final_oos_cost_{stress}x",
                "period": "final_oos",
                "stress_multiplier": float(stress),
                **result.summary,
            }
            final_rows.append(row)
            snapshots_frames.append(result.snapshots)
            orders_frames.append(result.orders)

        rolling_rows: list[dict[str, object]] = []
        for start in pd.to_datetime(
            ["2016-01-01", "2018-01-01", "2020-01-01", "2022-01-01", "2023-10-30"]
        ):
            end = min(
                start + pd.DateOffset(years=3) - pd.Timedelta(days=1),
                final_end,
            )
            result = engine.run(
                daily=daily,
                actions=actions,
                calendar=calendar,
                benchmark=benchmark,
                signal_bundle=bundle,
                rule_book=rule_book,
                cost_model=cost_model,
                scenario=f"rolling_{start.year}_{end.year}",
                stress_multiplier=Decimal("1"),
                with_costs=True,
                start_date=start,
                end_date=end,
            )
            rolling_rows.append(
                {
                    "scenario": f"rolling_{start.year}_{end.year}",
                    "window_start": start.date(),
                    "window_end": end.date(),
                    "stress_multiplier": 1.0,
                    **result.summary,
                }
            )
            snapshots_frames.append(result.snapshots)
            orders_frames.append(result.orders)

        final_summary = pd.DataFrame(final_rows)
        rolling = pd.DataFrame(rolling_rows)
        final_summary_output = final_summary.copy()
        rolling_output = rolling.copy()
        for frame in (final_summary_output, rolling_output):
            if "rejection_reasons" in frame:
                frame["rejection_reasons"] = frame["rejection_reasons"].map(
                    lambda value: json.dumps(value, ensure_ascii=False)
                )
        snapshots = pd.concat(snapshots_frames, ignore_index=True)
        orders = pd.concat(orders_frames, ignore_index=True)
        data_checks = self._data_quality_checks(daily, actions, universe)
        final_pass = bool(
            (final_summary["annualized_return"] > 0).all()
            and (final_summary["excess_return"] > 0).all()
            and (final_summary["max_drawdown"] >= -0.20).all()
        )
        errors: list[str] = []
        if not snapshots["reconciled"].all():
            errors.append("Forward validation contains an unreconciled snapshot")
        if not all(item["status"] == "ok" for item in data_checks):
            errors.append("Final data audit failed")
        payload = {
            "status": "ok" if not errors else "failed",
            "updated_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
            "config_sha256": frozen_hash,
            "algorithm_sha256": algorithm_fingerprint,
            "final_period": {
                "start": final_start.date(),
                "end": final_end.date(),
            },
            "final_validation_pass": final_pass,
            "final_scenarios": final_summary.to_dict(orient="records"),
            "rolling_windows": rolling.to_dict(orient="records"),
            "data_checks": data_checks,
            "errors": errors,
        }
        self.store.save_processed(final_summary_output, "forward_validation_scenarios")
        self.store.save_processed(rolling_output, "forward_validation_rolling")
        self.store.save_processed(snapshots, "forward_validation_snapshots")
        self.store.save_processed(orders, "forward_validation_orders")
        self.store.update_forward_validation_database(
            final_summary_output,
            rolling_output,
            snapshots,
            orders,
        )
        self.store.save_forward_validation_report(payload)
        self._write_report(
            self.project_root / "runs" / "forward_validation_v1" / "report.md",
            final_summary,
            rolling,
            data_checks,
            final_pass,
        )
        if errors:
            raise ValueError("Forward validation failed: " + "; ".join(errors))
        return payload

    @staticmethod
    def _data_quality_checks(
        daily: pd.DataFrame,
        actions: pd.DataFrame,
        universe: pd.DataFrame,
    ) -> list[dict[str, object]]:
        symbols = set(daily["symbol"].astype(str).str.zfill(6))
        universe_symbols = set(universe["symbol"].astype(str).str.zfill(6))
        checks = [
            {
                "check": "universe_symbols",
                "status": "ok" if symbols == universe_symbols else "failed",
                "detail": f"daily={len(symbols)}, universe={len(universe_symbols)}",
            },
            {
                "check": "duplicate_symbol_date",
                "status": "ok"
                if not daily.duplicated(["symbol", "trade_date"]).any()
                else "failed",
                "detail": str(int(daily.duplicated(["symbol", "trade_date"]).sum())),
            },
            {
                "check": "positive_adjusted_close",
                "status": "ok" if (daily["adj_close"] > 0).all() else "failed",
                "detail": str(int((daily["adj_close"] <= 0).sum())),
            },
            {
                "check": "action_symbols",
                "status": "ok"
                if set(actions["symbol"].astype(str).str.zfill(6)) <= universe_symbols
                else "failed",
                "detail": str(actions["symbol"].nunique()),
            },
        ]
        return checks

    def _write_report(
        self,
        path: Path,
        final_summary: pd.DataFrame,
        rolling: pd.DataFrame,
        data_checks: list[dict[str, object]],
        final_pass: bool,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        final_table = _markdown_table(
            final_summary[
                [
                    "scenario",
                    "total_return",
                    "annualized_return",
                    "max_drawdown",
                    "sharpe_ratio",
                    "excess_return",
                    "annualized_one_way_turnover",
                ]
            ]
        )
        rolling_table = _markdown_table(
            rolling[
                [
                    "window_start",
                    "window_end",
                    "total_return",
                    "max_drawdown",
                    "excess_return",
                ]
            ]
        )
        verdict = (
            "冻结规则通过最终样本外的收益、超额和回撤条件。"
            if final_pass
            else "冻结规则没有通过全部最终样本外条件。"
        )
        content = f"""# 冻结多资产策略前向验证

配置哈希：`hashlib.sha256(configs/frozen/multi_asset_v1.yaml)`在运行时固定，并写入本地manifest。

## 最终样本外

{final_table}

## 滚动窗口

{rolling_table}

## 数据审计

{_markdown_table(pd.DataFrame(data_checks))}

## 判定

{verdict}
"""
        path.write_text(content, encoding="utf-8")

    def _algorithm_fingerprint(self) -> str:
        paths = [
            self.config_path,
            self.project_root / "configs" / "trading_rules.csv",
            self.project_root / "src" / "a_share_quant" / "backtest" / "engine.py",
            self.project_root / "src" / "a_share_quant" / "execution" / "order.py",
            self.project_root / "src" / "a_share_quant" / "execution" / "costs.py",
            self.project_root / "src" / "a_share_quant" / "strategy" / "momentum.py",
        ]
        digest = hashlib.sha256()
        for path in paths:
            digest.update(path.read_bytes())
        return digest.hexdigest()

    def summary(self) -> dict[str, object]:
        import json

        path = self.store.processed_dir / "forward_validation_summary.json"
        if not path.is_file():
            raise FileNotFoundError("Run 'uv run quant-forward-check run' first")
        return json.loads(path.read_text(encoding="utf-8"))


def _markdown_table(frame: pd.DataFrame) -> str:
    headers = [str(column) for column in frame.columns]
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    for row in frame.itertuples(index=False):
        values = []
        for value in row:
            if isinstance(value, (float, np.floating)):
                values.append(f"{float(value):.6f}")
            else:
                values.append(str(value))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)

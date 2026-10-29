"""Development-only performance attribution and strategy failure diagnostics."""

from pathlib import Path

import numpy as np
import pandas as pd

from a_share_quant.data.storage import DataStore


class PerformanceDiagnosticsPipeline:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.store = DataStore(project_root)

    def run(self) -> dict[str, object]:
        development_end = pd.Timestamp("2023-10-29")
        snapshots = self.store.load_processed("robustness_snapshots")
        snapshots = snapshots[snapshots["scenario"] == "development"].copy()
        snapshots["trade_date"] = pd.to_datetime(snapshots["trade_date"])
        snapshots = snapshots[snapshots["trade_date"] <= development_end]
        benchmark = self.store.load_processed("benchmark_daily").copy()
        benchmark["trade_date"] = pd.to_datetime(benchmark["trade_date"])
        benchmark = benchmark.sort_values("trade_date")
        benchmark["benchmark_ma"] = benchmark["close"].rolling(200).mean()
        benchmark["market_up"] = benchmark["close"] > benchmark["benchmark_ma"]
        benchmark["benchmark_return"] = benchmark["close"].pct_change(fill_method=None)

        analysis = snapshots.merge(
            benchmark[["trade_date", "market_up", "benchmark_return"]],
            on="trade_date",
            how="left",
            validate="one_to_one",
        )
        analysis["strategy_return"] = analysis["equity"].pct_change(fill_method=None)
        regime = self._regime_summary(analysis)
        yearly = self._yearly_summary(analysis)
        drawdowns = self._drawdown_episodes(analysis)
        selection = self._selection_quality(development_end)
        trades = self._trade_summary(development_end)

        up = regime[regime["market_up"] == True].iloc[0]  # noqa: E712
        down = regime[regime["market_up"] == False].iloc[0]  # noqa: E712
        selected_edge = float(selection["selected_20d_mean"] - selection["eligible_20d_mean"])
        elapsed_years = max(
            (
                analysis["trade_date"].iloc[-1] - analysis["trade_date"].iloc[0]
            ).days
            / 365.25,
            1,
        )
        annual_turnover = float(analysis["rebalance_turnover"].sum() / elapsed_years)
        failure_drivers = [
            f"上涨市场收益捕获率为{float(up['up_capture']):.1%}，尚未跟上基准。",
            f"下跌市场收益捕获率为{float(down['down_capture']):.1%}，防守贡献为正。",
            f"选中ETF未来20日平均收益相对全部合格候选为{selected_edge:+.2%}，选股边际不足。",
            f"开发期年化单边换手为{annual_turnover:.2f}倍，成本侵蚀显著。",
        ]
        recommendations = [
            "先降低无信息量的周频权重漂移，改用月度或更高调仓阈值。",
            "将单一120日动量替换为多周期趋势共识，降低排名噪声。",
            "保留市场趋势过滤和风险降仓，但必须测试恢复后的再进入规则。",
            "新规则只在开发期内部训练/验证区间设计，最终近期样本外保持封存。",
        ]
        selection_summary = {
            key: value
            for key, value in selection.items()
            if key != "by_rank"
        }
        payload = {
            "status": "ok",
            "analyzed_through": development_end.date(),
            "regimes": regime.to_dict(orient="records"),
            "yearly": yearly.to_dict(orient="records"),
            "selection": selection_summary,
            "drawdowns": drawdowns.head(5).to_dict(orient="records"),
            "failure_drivers": failure_drivers,
            "recommendations": recommendations,
        }
        self.store.save_processed(regime, "diagnostics_regimes")
        self.store.save_processed(yearly, "diagnostics_yearly")
        self.store.save_processed(selection["by_rank"], "diagnostics_selection")
        self.store.save_processed(drawdowns, "diagnostics_drawdowns")
        self.store.save_processed(trades, "diagnostics_trades")
        self.store.update_diagnostics_database(
            regime,
            yearly,
            selection["by_rank"],
            drawdowns,
            trades,
        )
        self.store.save_diagnostics_report(payload)
        self._write_report(payload, trades)
        return payload

    @staticmethod
    def _regime_summary(frame: pd.DataFrame) -> pd.DataFrame:
        rows: list[dict[str, object]] = []
        for market_up, group in frame.groupby("market_up", dropna=False):
            strategy_returns = group["strategy_return"].dropna()
            benchmark_returns = group["benchmark_return"].dropna()
            strategy_return = float((1 + strategy_returns).prod() - 1)
            benchmark_return = float((1 + benchmark_returns).prod() - 1)
            rows.append(
                {
                    "market_up": bool(market_up) if pd.notna(market_up) else None,
                    "days": len(group),
                    "strategy_return": strategy_return,
                    "benchmark_return": benchmark_return,
                    "up_capture": strategy_return / benchmark_return
                    if benchmark_return > 0
                    else None,
                    "down_capture": strategy_return / benchmark_return
                    if benchmark_return < 0
                    else None,
                    "average_gross_exposure": float(group["gross_exposure"].mean()),
                    "average_holding_count": float(group["holding_count"].mean()),
                    "average_turnover": float(group["rebalance_turnover"].mean()),
                }
            )
        return pd.DataFrame(rows)

    @staticmethod
    def _yearly_summary(frame: pd.DataFrame) -> pd.DataFrame:
        rows = []
        for year, group in frame.groupby(frame["trade_date"].dt.year):
            strategy_returns = group["strategy_return"].dropna()
            benchmark_returns = group["benchmark_return"].dropna()
            strategy_return = float((1 + strategy_returns).prod() - 1)
            benchmark_return = float((1 + benchmark_returns).prod() - 1)
            rows.append(
                {
                    "year": int(year),
                    "strategy_return": strategy_return,
                    "benchmark_return": benchmark_return,
                    "excess_return": strategy_return - benchmark_return,
                    "average_exposure": float(group["gross_exposure"].mean()),
                    "one_way_turnover": float(group["rebalance_turnover"].sum()),
                }
            )
        return pd.DataFrame(rows)

    @staticmethod
    def _drawdown_episodes(frame: pd.DataFrame) -> pd.DataFrame:
        equity = frame["equity"].astype(float)
        dates = pd.to_datetime(frame["trade_date"])
        episodes: list[dict[str, object]] = []
        in_drawdown = False
        running_peak = float(equity.iloc[0])
        peak_date = dates.iloc[0]
        trough_date = dates.iloc[0]
        trough_value = 0.0
        for date, current_equity in zip(dates, equity, strict=True):
            if current_equity >= running_peak:
                if in_drawdown:
                    episodes.append(
                        {
                            "peak_date": peak_date.date(),
                            "trough_date": trough_date.date(),
                            "recovery_date": date.date(),
                            "max_drawdown": trough_value,
                            "duration_calendar_days": (date - peak_date).days,
                        }
                    )
                    in_drawdown = False
                running_peak = float(current_equity)
                peak_date = date
                continue

            drawdown = current_equity / running_peak - 1
            if not in_drawdown:
                in_drawdown = True
                trough_date = date
                trough_value = float(drawdown)
            elif drawdown < trough_value:
                trough_value = float(drawdown)
                trough_date = date
        if in_drawdown:
            episodes.append(
                {
                    "peak_date": peak_date.date(),
                    "trough_date": trough_date.date(),
                    "recovery_date": None,
                    "max_drawdown": trough_value,
                    "duration_calendar_days": (dates.iloc[-1] - peak_date).days,
                }
            )
        return pd.DataFrame(episodes).sort_values("max_drawdown").reset_index(drop=True)

    def _selection_quality(self, development_end: pd.Timestamp) -> dict[str, object]:
        signals = self.store.load_processed("backtest_signals")
        daily = self.store.load_processed("etf_daily")
        signals["signal_date"] = pd.to_datetime(signals["signal_date"])
        daily["trade_date"] = pd.to_datetime(daily["trade_date"])
        signals = signals[signals["signal_date"] <= development_end]
        close = daily.pivot(index="trade_date", columns="symbol", values="adj_close").sort_index()
        records: list[dict[str, object]] = []
        for signal_date, frame in signals.groupby("signal_date"):
            if signal_date not in close.index:
                continue
            position = close.index.get_loc(signal_date)
            for horizon in (20, 60):
                if position + horizon >= len(close):
                    continue
                forward = close.iloc[position + horizon] / close.iloc[position] - 1
                for row in frame.itertuples(index=False):
                    if row.symbol in forward.index:
                        records.append(
                            {
                                "signal_date": signal_date,
                                "symbol": row.symbol,
                                "rank": int(row.rank),
                                "selected": bool(row.selected),
                                "horizon": horizon,
                                "forward_return": float(forward[row.symbol]),
                            }
                        )
        detailed = pd.DataFrame(records)
        by_rank = detailed.groupby(
            ["horizon", "rank", "selected"]
        )["forward_return"].agg(["count", "mean", "median"]).reset_index()
        twenty = detailed[detailed["horizon"] == 20]
        selected_mean = float(twenty[twenty["selected"]]["forward_return"].mean())
        eligible_mean = float(twenty["forward_return"].mean())
        return {
            "by_rank": by_rank,
            "selected_20d_mean": selected_mean,
            "eligible_20d_mean": eligible_mean,
            "selected_20d_hit_rate": float(
                (twenty[twenty["selected"]]["forward_return"] > 0).mean()
            ),
        }

    def _trade_summary(self, development_end: pd.Timestamp) -> pd.DataFrame:
        orders = self.store.load_processed("backtest_orders")
        orders["execution_date"] = pd.to_datetime(orders["execution_date"])
        orders = orders[
            (orders["scenario"] == "cost_1x")
            & (orders["execution_date"] <= development_end)
            & (orders["status"] == "FILLED")
        ]
        return (
            orders.groupby("symbol")
            .agg(
                orders=("side", "size"),
                buys=("side", lambda values: int((values == "BUY").sum())),
                sells=("side", lambda values: int((values == "SELL").sum())),
                traded_notional=("gross_notional", "sum"),
                commission=("commission", "sum"),
                total_cost=("total_cost", "sum"),
            )
            .reset_index()
        )

    def _write_report(self, payload: dict[str, object], trades: pd.DataFrame) -> None:
        path = self.project_root / "runs" / "diagnostics_v1" / "report.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        regimes = _markdown_table(pd.DataFrame(payload["regimes"]))
        yearly = _markdown_table(pd.DataFrame(payload["yearly"]))
        drawdowns = _markdown_table(pd.DataFrame(payload["drawdowns"]))
        trades_table = _markdown_table(trades)
        drivers = "\n".join(f"- {item}" for item in payload["failure_drivers"])
        recommendations = "\n".join(f"- {item}" for item in payload["recommendations"])
        content = f"""# 第一版策略失败归因

## 市场环境

{regimes}

## 年度表现

{yearly}

## 最大回撤期间

{drawdowns}

## 分ETF交易成本

{trades_table}

## 归因结论

{drivers}

## 结构性改进方向

{recommendations}
"""
        path.write_text(content, encoding="utf-8")

    def summary(self) -> dict[str, object]:
        import json

        path = self.store.processed_dir / "diagnostics_summary.json"
        if not path.is_file():
            raise FileNotFoundError("Run 'uv run quant-diagnose run' first")
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

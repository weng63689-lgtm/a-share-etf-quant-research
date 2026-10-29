"""Cross-sectional factor efficacy research on train and internal validation."""

from pathlib import Path

import numpy as np
import pandas as pd

from a_share_quant.data.storage import DataStore
from a_share_quant.strategy.momentum import rebalance_signal_dates

FACTOR_COLUMNS = {
    "momentum_120": 1,
    "momentum_blend": 1,
    "trend_strength": 1,
    "low_volatility": 1,
    "composite": 1,
}


class FactorResearchPipeline:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.store = DataStore(project_root)

    def run(
        self,
        *,
        daily_dataset: str = "etf_daily",
        output_prefix: str = "",
    ) -> dict[str, object]:
        daily = self.store.load_processed(daily_dataset).copy()
        daily["trade_date"] = pd.to_datetime(daily["trade_date"])
        calendar = self.store.load_processed("trading_calendar")
        signal_dates = rebalance_signal_dates(calendar, "monthly")
        factor_rows = self._factor_panel(daily, signal_dates)
        ic_detail, summary = self._factor_metrics(factor_rows)

        train = summary[summary["period"] == "train"]
        validation = summary[summary["period"] == "validation"]
        train_eligible = train[
            (train["horizon"] == 20) & (train["observations"] >= 12)
        ]
        selected_factor = (
            str(train_eligible.sort_values("mean_ic", ascending=False).iloc[0]["factor"])
            if not train_eligible.empty
            else "none"
        )
        payload = {
            "status": "ok",
            "updated_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
            "selection_rule": "highest train 20-day mean rank IC with at least 12 observations",
            "selected_factor_from_train": selected_factor,
            "train_summary": train.to_dict(orient="records"),
            "validation_summary": validation.to_dict(orient="records"),
        }
        self.store.save_processed(factor_rows, f"{output_prefix}factor_panel")
        self.store.save_processed(ic_detail, f"{output_prefix}factor_ic_detail")
        self.store.save_processed(summary, f"{output_prefix}factor_ic_summary")
        self.store.update_factor_database(
            factor_rows,
            ic_detail,
            summary,
            prefix=output_prefix,
        )
        self.store.save_factor_report(payload, prefix=output_prefix)
        self._write_report(
            self.project_root
            / "runs"
            / (
                "factor_research_expanded"
                if output_prefix
                else "factor_research_v1"
            )
            / "report.md",
            train,
            validation,
            selected_factor,
        )
        return payload

    @staticmethod
    def _factor_panel(
        daily: pd.DataFrame,
        signal_dates: pd.DatetimeIndex,
    ) -> pd.DataFrame:
        close = daily.pivot(index="trade_date", columns="symbol", values="adj_close").sort_index()
        returns = close.pct_change(fill_method=None)
        rows: list[dict[str, object]] = []
        for signal_date in signal_dates:
            if signal_date not in close.index:
                continue
            position = close.index.get_loc(signal_date)
            history = close.iloc[: position + 1]
            for symbol in close.columns:
                series = history[symbol].dropna()
                if len(series) < 241:
                    continue
                momentum_60 = series.iloc[-1] / series.iloc[-61] - 1
                momentum_120 = series.iloc[-1] / series.iloc[-121] - 1
                momentum_240 = series.iloc[-1] / series.iloc[-241] - 1
                volatility = (
                    returns[symbol].loc[:signal_date].tail(20).std(ddof=0) * np.sqrt(252)
                )
                if not np.isfinite(volatility) or volatility <= 0:
                    continue
                trend_strength = np.mean(
                    [
                        series.iloc[-1] / series.tail(window).mean() - 1
                        for window in (60, 120, 200)
                    ]
                )
                row = {
                    "signal_date": signal_date,
                    "symbol": symbol,
                    "momentum_60": float(momentum_60),
                    "momentum_120": float(momentum_120),
                    "momentum_240": float(momentum_240),
                    "momentum_blend": float(
                        np.mean([momentum_60, momentum_120, momentum_240])
                    ),
                    "volatility": float(volatility),
                    "low_volatility": float(-volatility),
                    "trend_strength": float(trend_strength),
                }
                for horizon in (20, 60):
                    if position + horizon < len(close):
                        row[f"forward_{horizon}d"] = float(
                            close[symbol].iloc[position + horizon] / series.iloc[-1] - 1
                        )
                    else:
                        row[f"forward_{horizon}d"] = np.nan
                rows.append(row)
        panel = pd.DataFrame(rows)
        if panel.empty:
            return panel
        for column in ("momentum_blend", "trend_strength", "low_volatility"):
            panel[f"rank_{column}"] = panel.groupby("signal_date")[column].rank(pct=True)
        panel["composite"] = panel[
            ["rank_momentum_blend", "rank_trend_strength", "rank_low_volatility"]
        ].mean(axis=1)
        return panel

    @staticmethod
    def _factor_metrics(
        panel: pd.DataFrame,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        detail_rows: list[dict[str, object]] = []
        for signal_date, frame in panel.groupby("signal_date"):
            for factor, sign in FACTOR_COLUMNS.items():
                for horizon in (20, 60):
                    target = f"forward_{horizon}d"
                    sample = frame[[factor, target]].dropna()
                    if len(sample) < 4:
                        continue
                    ic = sample[factor].rank().corr(sample[target].rank())
                    sorted_sample = sample.sort_values(factor)
                    top_count = max(1, len(sorted_sample) // 3)
                    top_return = float(sorted_sample[target].tail(top_count).mean())
                    bottom_return = float(sorted_sample[target].head(top_count).mean())
                    detail_rows.append(
                        {
                            "signal_date": signal_date,
                            "factor": factor,
                            "expected_sign": sign,
                            "horizon": horizon,
                            "rank_ic": float(ic),
                            "top_minus_bottom": top_return - bottom_return,
                            "observations": len(sample),
                        }
                    )
        detail = pd.DataFrame(detail_rows)
        detail["period"] = np.where(
            detail["signal_date"] <= pd.Timestamp("2018-12-31"),
            "train",
            "validation",
        )
        summary_rows: list[dict[str, object]] = []
        for (period, factor, horizon), frame in detail.groupby(
            ["period", "factor", "horizon"]
        ):
            sign = FACTOR_COLUMNS[factor]
            signed_ic = frame["rank_ic"] * sign
            signed_spread = frame["top_minus_bottom"] * sign
            summary_rows.append(
                {
                    "period": period,
                    "factor": factor,
                    "horizon": horizon,
                    "observations": len(frame),
                    "mean_ic": float(signed_ic.mean()),
                    "ic_std": float(signed_ic.std(ddof=0)),
                    "ic_positive_rate": float((signed_ic > 0).mean()),
                    "top_minus_bottom_mean": float(signed_spread.mean()),
                    "top_minus_bottom_positive_rate": float((signed_spread > 0).mean()),
                }
            )
        return detail, pd.DataFrame(summary_rows)

    def _write_report(
        self,
        path: Path,
        train: pd.DataFrame,
        validation: pd.DataFrame,
        selected_factor: str,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        content = f"""# ETF横截面因子有效性研究

## 因子

- `momentum_120`：120日动量。
- `momentum_blend`：60、120、240日动量平均。
- `trend_strength`：相对60、120、200日均线的平均偏离。
- `low_volatility`：20日年化波动率的反向值。
- `composite`：上述动量、趋势和低波动横截面排名的等权组合。

## 训练期结果

{_markdown_table(train)}

训练期规则选出的因子方向：`{selected_factor}`。

选择规则只看训练期20日平均Rank IC，且要求至少12个截面观测，不使用内部验证和最终样本外。

## 内部验证结果

{_markdown_table(validation)}

因子研究只判断信息含量，不直接产生交易规则。负IC或正负不稳定的因子不能作为新策略核心。
"""
        path.write_text(content, encoding="utf-8")

    def summary(self, *, output_prefix: str = "") -> dict[str, object]:
        import json

        path = self.store.processed_dir / f"{output_prefix}factor_research_summary.json"
        if not path.is_file():
            raise FileNotFoundError("Run 'uv run quant-factor run' first")
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

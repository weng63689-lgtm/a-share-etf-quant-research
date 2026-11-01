"""Pre-registered domestic ETF asset-allocation optimization experiments."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd

from a_share_quant.accounting.config import load_account_config
from a_share_quant.backtest.engine import StrategyBacktestEngine
from a_share_quant.data.storage import DataStore
from a_share_quant.execution.costs import load_cost_model
from a_share_quant.execution.rules import load_trading_rules
from a_share_quant.strategy.config import StrategyConfig, load_strategy_config
from a_share_quant.strategy.momentum import (
    SignalBundle,
    build_signals,
    compute_indicators,
    rebalance_signal_dates,
)

EQUITY = "DOMESTIC_EQUITY_ETF"
BOND = "DOMESTIC_BOND_ETF"
GOLD = "DOMESTIC_GOLD_ETF"
MONEY = "DOMESTIC_MONEY_MARKET_ETF"
CORE = "CORE_EQUITY"
SATELLITE = "SATELLITE_EQUITY"

CORE_EQUITY_SYMBOLS = {
    "510050",
    "510300",
    "510500",
    "512100",
    "588000",
    "159915",
}
SATELLITE_EQUITY_SYMBOLS = {
    "512890",
    "512880",
    "512800",
    "512660",
    "159928",
    "159949",
    "512170",
    "512480",
}


@dataclass(frozen=True)
class AllocationVariant:
    name: str
    risk_on: dict[str, float]
    elevated_volatility: dict[str, float]
    risk_off: dict[str, float]
    trend_days: int = 200
    volatility_days: int = 20
    high_volatility_threshold: float = 0.22
    equity_holdings: int = 2
    bond_holdings: int = 1


@dataclass(frozen=True)
class CoreSatelliteVariant:
    name: str
    risk_on: dict[str, float]
    elevated_volatility: dict[str, float]
    risk_off: dict[str, float]


ALLOCATION_VARIANTS = (
    AllocationVariant(
        name="asset_class_conservative",
        risk_on={EQUITY: 0.55, BOND: 0.25, GOLD: 0.10, MONEY: 0.05},
        elevated_volatility={EQUITY: 0.40, BOND: 0.35, GOLD: 0.05, MONEY: 0.10},
        risk_off={EQUITY: 0.15, BOND: 0.45, GOLD: 0.05, MONEY: 0.25},
    ),
    AllocationVariant(
        name="asset_class_balanced",
        risk_on={EQUITY: 0.65, BOND: 0.20, GOLD: 0.10, MONEY: 0.05},
        elevated_volatility={EQUITY: 0.45, BOND: 0.35, GOLD: 0.05, MONEY: 0.10},
        risk_off={EQUITY: 0.20, BOND: 0.45, GOLD: 0.05, MONEY: 0.25},
    ),
    AllocationVariant(
        name="asset_class_growth",
        risk_on={EQUITY: 0.75, BOND: 0.15, GOLD: 0.05, MONEY: 0.05},
        elevated_volatility={EQUITY: 0.55, BOND: 0.25, GOLD: 0.05, MONEY: 0.10},
        risk_off={EQUITY: 0.25, BOND: 0.40, GOLD: 0.05, MONEY: 0.25},
    ),
)

CORE_SATELLITE_VARIANTS = (
    CoreSatelliteVariant(
        name="core_satellite_10",
        risk_on={CORE: 0.45, SATELLITE: 0.20, BOND: 0.15, GOLD: 0.05, MONEY: 0.10},
        elevated_volatility={
            CORE: 0.30,
            SATELLITE: 0.10,
            BOND: 0.30,
            GOLD: 0.05,
            MONEY: 0.20,
        },
        risk_off={CORE: 0.05, SATELLITE: 0.0, BOND: 0.35, GOLD: 0.05, MONEY: 0.50},
    ),
    CoreSatelliteVariant(
        name="core_satellite_12",
        risk_on={CORE: 0.45, SATELLITE: 0.20, BOND: 0.15, GOLD: 0.05, MONEY: 0.10},
        elevated_volatility={
            CORE: 0.30,
            SATELLITE: 0.10,
            BOND: 0.30,
            GOLD: 0.05,
            MONEY: 0.20,
        },
        risk_off={CORE: 0.05, SATELLITE: 0.0, BOND: 0.35, GOLD: 0.05, MONEY: 0.50},
    ),
)


def _bounded_inverse_volatility(
    rows: pd.DataFrame,
    budget: float,
    max_weight: float,
) -> dict[str, float]:
    if rows.empty or budget <= 0:
        return {}
    inverse = 1 / rows.set_index("symbol")["volatility"].astype(float)
    remaining = set(inverse.index)
    weights: dict[str, float] = {}
    remaining_budget = budget
    while remaining and remaining_budget > 0:
        total_inverse = float(inverse.loc[list(remaining)].sum())
        proposed = {
            symbol: remaining_budget * float(inverse.loc[symbol]) / total_inverse
            for symbol in remaining
        }
        over = [symbol for symbol, weight in proposed.items() if weight > max_weight]
        if not over:
            weights.update(proposed)
            break
        for symbol in over:
            weights[symbol] = max_weight
            remaining.remove(symbol)
            remaining_budget -= max_weight
    return weights


def _portfolio_volatility_scale(
    weights: dict[str, float],
    returns: pd.DataFrame,
    *,
    signal_date: pd.Timestamp,
    covariance_days: int,
    target_volatility: float,
) -> float:
    if not weights:
        return 1.0
    symbols = list(weights)
    covariance_frame = returns.loc[:signal_date].tail(covariance_days)[symbols]
    if len(covariance_frame) < covariance_days:
        return 1.0
    covariance = covariance_frame.cov().to_numpy(dtype=float) * 252
    vector = np.array([weights[symbol] for symbol in symbols], dtype=float)
    portfolio_volatility = float(np.sqrt(vector @ covariance @ vector))
    if not np.isfinite(portfolio_volatility) or portfolio_volatility <= 0:
        return 1.0
    return min(1.0, target_volatility / portfolio_volatility)


def build_asset_class_signals(
    daily: pd.DataFrame,
    calendar: pd.DataFrame,
    benchmark: pd.DataFrame,
    universe: pd.DataFrame,
    config: StrategyConfig,
    variant: AllocationVariant,
) -> SignalBundle:
    """Build monthly asset-class targets using only information through signal close."""
    indicators = compute_indicators(daily, config)
    open_dates = pd.DatetimeIndex(
        pd.to_datetime(calendar.loc[calendar["is_open"], "calendar_date"]).sort_values()
    )
    signal_dates = rebalance_signal_dates(calendar, "monthly")
    asset_classes = (
        universe.assign(
            symbol=universe["symbol"].astype(str).str.zfill(6),
            asset_class=universe["asset_class"].astype(str),
        )[["symbol", "asset_class"]]
    )
    indicators = indicators.merge(
        asset_classes,
        on="symbol",
        how="left",
        validate="many_to_one",
    )
    returns = indicators.pivot(
        index="trade_date",
        columns="symbol",
        values="adj_close",
    ).pct_change(fill_method=None)

    benchmark_frame = benchmark.sort_values("trade_date").copy()
    benchmark_frame["trade_date"] = pd.to_datetime(benchmark_frame["trade_date"])
    benchmark_close = benchmark_frame.set_index("trade_date")["close"].astype(float)
    benchmark_ma = benchmark_close.rolling(variant.trend_days).mean()
    benchmark_volatility = (
        benchmark_close.pct_change(fill_method=None)
        .rolling(variant.volatility_days)
        .std(ddof=0)
        * np.sqrt(252)
    )

    execution_targets: dict[pd.Timestamp, dict[str, float]] = {}
    execution_signals: dict[pd.Timestamp, pd.Timestamp] = {}
    target_rows: list[dict[str, object]] = []
    ranking_rows: list[dict[str, object]] = []
    previous_targets: set[str] = set()

    for signal_date in signal_dates:
        future_dates = open_dates[open_dates > signal_date]
        if future_dates.empty or signal_date not in benchmark_close.index:
            continue
        execution_date = pd.Timestamp(future_dates[0]).normalize()
        benchmark_price = float(benchmark_close.loc[signal_date])
        moving_average = benchmark_ma.loc[signal_date]
        annualized_volatility = benchmark_volatility.loc[signal_date]
        if not np.isfinite(moving_average) or not np.isfinite(annualized_volatility):
            continue
        if benchmark_price <= moving_average:
            regime = "risk_off"
            budgets = variant.risk_off
        elif annualized_volatility > variant.high_volatility_threshold:
            regime = "elevated_volatility"
            budgets = variant.elevated_volatility
        else:
            regime = "risk_on"
            budgets = variant.risk_on

        rows = indicators[
            (indicators["trade_date"] == signal_date) & indicators["eligible"]
        ].copy()
        rows = rows.sort_values(["score", "symbol"], ascending=[False, True])
        for rank, row in enumerate(rows.itertuples(index=False), start=1):
            ranking_rows.append(
                {
                    "signal_date": signal_date,
                    "execution_date": execution_date,
                    "symbol": row.symbol,
                    "asset_class": row.asset_class,
                    "rank": rank,
                    "momentum": row.momentum,
                    "volatility": row.volatility,
                    "score": row.score,
                    "regime": regime,
                    "retained": row.symbol in previous_targets,
                }
            )

        holdings: dict[str, float] = {}
        class_limits = {
            EQUITY: variant.equity_holdings,
            BOND: variant.bond_holdings,
            GOLD: 1,
            MONEY: 1,
        }
        class_caps = {EQUITY: 0.35, BOND: 0.30, GOLD: 0.18, MONEY: 0.30}
        for asset_class, budget in budgets.items():
            class_rows = rows[rows["asset_class"] == asset_class].head(
                class_limits[asset_class]
            )
            holdings.update(
                _bounded_inverse_volatility(
                    class_rows,
                    float(budget),
                    class_caps[asset_class],
                )
            )

        scale = _portfolio_volatility_scale(
            holdings,
            returns,
            signal_date=signal_date,
            covariance_days=config.covariance_days,
            target_volatility=config.target_volatility,
        )
        weights = {
            symbol: weight * scale
            for symbol, weight in holdings.items()
            if weight * scale > 0
        }
        total_weight = sum(weights.values())
        if total_weight > 1:
            weights = {symbol: weight / total_weight for symbol, weight in weights.items()}
        previous_targets = set(weights)
        execution_targets[execution_date] = weights
        execution_signals[execution_date] = signal_date
        for symbol, weight in weights.items():
            asset_class = str(
                asset_classes.loc[asset_classes["symbol"] == symbol, "asset_class"].iloc[0]
            )
            target_rows.append(
                {
                    "signal_date": signal_date,
                    "execution_date": execution_date,
                    "symbol": symbol,
                    "asset_class": asset_class,
                    "target_weight": weight,
                    "regime": regime,
                    "volatility_scale": scale,
                }
            )

    target_frame = pd.DataFrame(target_rows)
    ranking_frame = pd.DataFrame(ranking_rows)
    exposure = (
        target_frame.groupby("execution_date")["target_weight"].sum()
        if not target_frame.empty
        else pd.Series(dtype=float)
    )
    if not exposure.empty and bool((exposure > 1 + 1e-12).any()):
        raise ValueError("Asset-class target weights exceed 100%")
    return SignalBundle(
        indicators=indicators,
        rankings=ranking_frame,
        targets=target_frame,
        execution_targets=execution_targets,
        execution_signals=execution_signals,
    )


def build_core_satellite_signals(
    daily: pd.DataFrame,
    calendar: pd.DataFrame,
    benchmark: pd.DataFrame,
    universe: pd.DataFrame,
    config: StrategyConfig,
    variant: CoreSatelliteVariant,
) -> SignalBundle:
    """Build broad-core plus defensive-satellite targets with trend consensus."""
    indicators = compute_indicators(daily, config)
    open_dates = pd.DatetimeIndex(
        pd.to_datetime(calendar.loc[calendar["is_open"], "calendar_date"]).sort_values()
    )
    signal_dates = rebalance_signal_dates(calendar, "monthly")
    asset_classes = (
        universe.assign(
            symbol=universe["symbol"].astype(str).str.zfill(6),
            asset_class=universe["asset_class"].astype(str),
        )[["symbol", "asset_class"]]
    )
    indicators = indicators.merge(
        asset_classes,
        on="symbol",
        how="left",
        validate="many_to_one",
    )
    returns = indicators.pivot(
        index="trade_date",
        columns="symbol",
        values="adj_close",
    ).pct_change(fill_method=None)

    benchmark_frame = benchmark.sort_values("trade_date").copy()
    benchmark_frame["trade_date"] = pd.to_datetime(benchmark_frame["trade_date"])
    benchmark_close = benchmark_frame.set_index("trade_date")["close"].astype(float)
    benchmark_ma = benchmark_close.rolling(config.benchmark_trend_days).mean()
    benchmark_volatility = (
        benchmark_close.pct_change(fill_method=None)
        .rolling(20)
        .std(ddof=0)
        * np.sqrt(252)
    )

    execution_targets: dict[pd.Timestamp, dict[str, float]] = {}
    execution_signals: dict[pd.Timestamp, pd.Timestamp] = {}
    target_rows: list[dict[str, object]] = []
    ranking_rows: list[dict[str, object]] = []
    for signal_date in signal_dates:
        future_dates = open_dates[open_dates > signal_date]
        if future_dates.empty or signal_date not in benchmark_close.index:
            continue
        execution_date = pd.Timestamp(future_dates[0]).normalize()
        benchmark_price = float(benchmark_close.loc[signal_date])
        moving_average = benchmark_ma.loc[signal_date]
        annualized_volatility = benchmark_volatility.loc[signal_date]
        if not np.isfinite(moving_average) or not np.isfinite(annualized_volatility):
            continue
        if benchmark_price <= moving_average:
            regime = "risk_off"
            budgets = variant.risk_off
        elif annualized_volatility > 0.22:
            regime = "elevated_volatility"
            budgets = variant.elevated_volatility
        else:
            regime = "risk_on"
            budgets = variant.risk_on

        rows = indicators[
            (indicators["trade_date"] == signal_date) & indicators["eligible"]
        ].copy()
        rows = rows.sort_values(["score", "symbol"], ascending=[False, True])
        for rank, row in enumerate(rows.itertuples(index=False), start=1):
            ranking_rows.append(
                {
                    "signal_date": signal_date,
                    "execution_date": execution_date,
                    "symbol": row.symbol,
                    "asset_class": row.asset_class,
                    "rank": rank,
                    "momentum": row.momentum,
                    "volatility": row.volatility,
                    "score": row.score,
                    "trend_consensus": row.trend_vote,
                    "regime": regime,
                }
            )

        core_rows = rows[rows["symbol"].isin(CORE_EQUITY_SYMBOLS)].head(2)
        satellite_rows = rows[
            rows["symbol"].isin(SATELLITE_EQUITY_SYMBOLS)
        ].head(1)
        bond_rows = rows[rows["asset_class"] == BOND].head(1)
        gold_rows = rows[rows["asset_class"] == GOLD].head(1)
        money_rows = rows[rows["asset_class"] == MONEY].head(1)
        holdings: dict[str, float] = {}
        sleeves = (
            (CORE, core_rows, 0.25),
            (SATELLITE, satellite_rows, 0.18),
            (BOND, bond_rows, 0.35),
            (GOLD, gold_rows, 0.15),
            (MONEY, money_rows, 0.35),
        )
        for sleeve, sleeve_rows, max_weight in sleeves:
            holdings.update(
                _bounded_inverse_volatility(
                    sleeve_rows,
                    float(budgets.get(sleeve, 0.0)),
                    max_weight,
                )
            )
        scale = _portfolio_volatility_scale(
            holdings,
            returns,
            signal_date=signal_date,
            covariance_days=config.covariance_days,
            target_volatility=config.target_volatility,
        )
        weights = {
            symbol: weight * scale
            for symbol, weight in holdings.items()
            if weight * scale > 0
        }
        total_weight = sum(weights.values())
        if total_weight > 1:
            weights = {symbol: weight / total_weight for symbol, weight in weights.items()}
        execution_targets[execution_date] = weights
        execution_signals[execution_date] = signal_date
        for symbol, weight in weights.items():
            target_rows.append(
                {
                    "signal_date": signal_date,
                    "execution_date": execution_date,
                    "symbol": symbol,
                    "target_weight": weight,
                    "regime": regime,
                    "volatility_scale": scale,
                }
            )

    target_frame = pd.DataFrame(target_rows)
    ranking_frame = pd.DataFrame(ranking_rows)
    exposure = (
        target_frame.groupby("execution_date")["target_weight"].sum()
        if not target_frame.empty
        else pd.Series(dtype=float)
    )
    if not exposure.empty and bool((exposure > 1 + 1e-12).any()):
        raise ValueError("Core-satellite target weights exceed 100%")
    return SignalBundle(
        indicators=indicators,
        rankings=ranking_frame,
        targets=target_frame,
        execution_targets=execution_targets,
        execution_signals=execution_signals,
    )


class AssetClassOptimizationPipeline:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.store = DataStore(project_root)

    def run(self) -> dict[str, object]:
        daily = self.store.load_processed("multi_asset_research_etf_daily")
        actions = self.store.load_processed(
            "multi_asset_research_etf_corporate_actions"
        )
        calendar = self.store.load_processed("trading_calendar")
        benchmark = self.store.load_processed("benchmark_daily")
        universe = self.store.load_universe("research_universe_v3.csv")
        config = load_strategy_config(
            self.project_root,
            override_file=self.project_root / "configs" / "frozen" / "multi_asset_v1.yaml",
        )
        account_config = load_account_config(self.project_root)
        cost_model = load_cost_model(self.project_root)
        rule_book = load_trading_rules(
            self.project_root,
            universe_symbols=set(universe["symbol"].astype(str).str.zfill(6)),
        )

        baseline_bundle = build_signals(
            daily,
            calendar,
            config,
            benchmark=benchmark,
        )
        candidates: list[tuple[str, SignalBundle, bool, StrategyConfig]] = [
            ("frozen_v1_without_drawdown_control", baseline_bundle, False, config),
            ("frozen_v1_with_drawdown_control", baseline_bundle, True, config),
        ]
        for variant in ALLOCATION_VARIANTS:
            candidates.append(
                (
                    variant.name,
                    build_asset_class_signals(
                        daily,
                        calendar,
                        benchmark,
                        universe,
                        config,
                        variant,
                    ),
                    True,
                    config,
                )
            )
        risk_variants = (
            (
                "position_stop_06",
                replace(config, position_stop_loss_pct=0.06),
            ),
            (
                "position_stop_08",
                replace(config, position_stop_loss_pct=0.08),
            ),
            (
                "diversified_4_cap25",
                replace(
                    config,
                    selection_count=4,
                    max_single_weight=0.25,
                ),
            ),
            (
                "diversified_5_cap20",
                replace(
                    config,
                    selection_count=5,
                    max_single_weight=0.20,
                ),
            ),
            (
                "diversified_5_cap20_stop08",
                replace(
                    config,
                    selection_count=5,
                    max_single_weight=0.20,
                    position_stop_loss_pct=0.08,
                ),
            ),
        )
        for name, candidate_config in risk_variants:
            candidates.append(
                (
                    name,
                    build_signals(
                        daily,
                        calendar,
                        candidate_config,
                        benchmark=benchmark,
                    ),
                    True,
                    candidate_config,
                )
            )
        core_base_config = replace(
            config,
            momentum_windows=(60, 120, 240),
            trend_windows=(60, 120, 240),
            trend_consensus_ratio=2 / 3,
            min_rebalance_turnover=0.08,
        )
        core_configs = (
            (
                "core_satellite_10",
                replace(core_base_config, target_volatility=0.10),
            ),
            (
                "core_satellite_12",
                replace(core_base_config, target_volatility=0.12),
            ),
            (
                "core_satellite_12_trailing",
                replace(
                    core_base_config,
                    target_volatility=0.12,
                    trailing_stop_pct=0.06,
                    trailing_activation_pct=0.05,
                ),
            ),
        )
        core_variant_by_name = {
            variant.name: variant for variant in CORE_SATELLITE_VARIANTS
        }
        for name, candidate_config in core_configs:
            base_variant_name = (
                "core_satellite_10" if name == "core_satellite_10" else "core_satellite_12"
            )
            variant = core_variant_by_name[base_variant_name]
            candidates.append(
                (
                    name,
                    build_core_satellite_signals(
                        daily,
                        calendar,
                        benchmark,
                        universe,
                        candidate_config,
                        variant,
                    ),
                    True,
                    candidate_config,
                )
            )

        periods = [
            ("train", None, pd.Timestamp("2018-12-31")),
            ("validation", pd.Timestamp("2019-01-01"), pd.Timestamp("2023-10-29")),
        ]
        summaries: list[dict[str, object]] = []
        snapshots_frames: list[pd.DataFrame] = []
        orders_frames: list[pd.DataFrame] = []
        for name, bundle, drawdown_control, candidate_config in candidates:
            for period, start, end in periods:
                result = self._run(
                    daily=daily,
                    actions=actions,
                    calendar=calendar,
                    benchmark=benchmark,
                    bundle=bundle,
                    rule_book=rule_book,
                    cost_model=cost_model,
                    config=candidate_config,
                    account_config=account_config,
                    drawdown_control=drawdown_control,
                    scenario=f"{name}_{period}_cost_1x",
                    stress=Decimal("1"),
                    start=start,
                    end=end,
                )
                summaries.append(
                    {
                        "candidate": name,
                        "period": period,
                        "stress_multiplier": 1.0,
                        "drawdown_control": drawdown_control,
                        **result.summary,
                    }
                )
                snapshots_frames.append(result.snapshots)
                orders_frames.append(result.orders)

        summary = pd.DataFrame(summaries)
        validation = summary[summary["period"] == "validation"].copy()
        preliminary = validation[
            (validation["total_return"] > 0)
            & (validation["excess_return"] > 0)
            & (validation["max_drawdown"] >= -0.12)
        ].sort_values("sharpe_ratio", ascending=False)

        eligible: list[str] = []
        for candidate in preliminary["candidate"]:
            matching = next(item for item in candidates if item[0] == candidate)
            _, bundle, drawdown_control, candidate_config = matching
            stress_rows = []
            for stress in (Decimal("2"), Decimal("3")):
                result = self._run(
                    daily=daily,
                    actions=actions,
                    calendar=calendar,
                    benchmark=benchmark,
                    bundle=bundle,
                    rule_book=rule_book,
                    cost_model=cost_model,
                    config=candidate_config,
                    account_config=account_config,
                    drawdown_control=drawdown_control,
                    scenario=f"{candidate}_validation_cost_{stress}x",
                    stress=stress,
                    start=pd.Timestamp("2019-01-01"),
                    end=pd.Timestamp("2023-10-29"),
                )
                row = {
                    "candidate": candidate,
                    "period": "validation",
                    "stress_multiplier": float(stress),
                    "drawdown_control": drawdown_control,
                    **result.summary,
                }
                summaries.append(row)
                stress_rows.append(row)
                snapshots_frames.append(result.snapshots)
                orders_frames.append(result.orders)
            if all(
                row["total_return"] > 0
                and row["excess_return"] > 0
                and row["max_drawdown"] >= -0.15
                for row in stress_rows
            ):
                eligible.append(candidate)

        winner = eligible[0] if eligible else None
        final_rows: list[dict[str, object]] = []
        if winner is not None:
            _, bundle, drawdown_control, candidate_config = next(
                item for item in candidates if item[0] == winner
            )
            for stress in (Decimal("1"), Decimal("2"), Decimal("3")):
                result = self._run(
                    daily=daily,
                    actions=actions,
                    calendar=calendar,
                    benchmark=benchmark,
                    bundle=bundle,
                    rule_book=rule_book,
                    cost_model=cost_model,
                    config=candidate_config,
                    account_config=account_config,
                    drawdown_control=drawdown_control,
                    scenario=f"{winner}_final_oos_cost_{stress}x",
                    stress=stress,
                    start=pd.Timestamp("2023-10-30"),
                    end=pd.Timestamp("2026-10-29"),
                )
                row = {
                    "candidate": winner,
                    "period": "final_oos",
                    "stress_multiplier": float(stress),
                    "drawdown_control": drawdown_control,
                    **result.summary,
                }
                summaries.append(row)
                final_rows.append(row)
                snapshots_frames.append(result.snapshots)
                orders_frames.append(result.orders)

        final_pass = bool(
            final_rows
            and all(
                row["total_return"] > 0
                and row["excess_return"] > 0
                and row["max_drawdown"] >= -0.15
                for row in final_rows
            )
        )
        updated_summary = pd.DataFrame(summaries)
        snapshots = pd.concat(snapshots_frames, ignore_index=True)
        orders = pd.concat(orders_frames, ignore_index=True)
        errors: list[str] = []
        if not snapshots["reconciled"].all():
            errors.append("Optimization backtest contains unreconciled snapshots")
        if not np.isfinite(updated_summary["annualized_return"]).all():
            errors.append("Optimization summary contains non-finite returns")

        payload = {
            "status": "ok" if not errors else "failed",
            "updated_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
            "method": "pre_registered_asset_class_core_satellite_and_risk_variants",
            "split": {
                "train_end": "2018-12-31",
                "validation_start": "2019-01-01",
                "validation_end": "2023-10-29",
                "final_oos_start": "2023-10-30",
                "final_oos_end": "2026-10-29",
            },
            "selection_rule": {
                "validation_positive_return": True,
                "validation_positive_excess_return": True,
                "validation_max_drawdown": -0.12,
                "cost_stress_max_drawdown": -0.15,
                "ranking_metric": "sharpe_ratio",
            },
            "eligible_candidates": eligible,
            "selected_candidate": winner,
            "final_oos_pass": final_pass,
            "summary": updated_summary.to_dict(orient="records"),
            "errors": errors,
        }
        self.store.save_processed(
            updated_summary,
            "asset_class_optimization_scenarios",
        )
        self.store.save_processed(
            snapshots,
            "asset_class_optimization_snapshots",
        )
        self.store.save_processed(
            orders,
            "asset_class_optimization_orders",
        )
        report_path = self.project_root / "data" / "processed" / (
            "asset_class_optimization_summary.json"
        )
        report_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        self._write_report(payload, updated_summary)
        if errors:
            raise ValueError("Optimization validation failed: " + "; ".join(errors))
        return payload

    def _run(
        self,
        *,
        daily: pd.DataFrame,
        actions: pd.DataFrame,
        calendar: pd.DataFrame,
        benchmark: pd.DataFrame,
        bundle: SignalBundle,
        rule_book: object,
        cost_model: object,
        config: StrategyConfig,
        account_config: object,
        drawdown_control: bool,
        scenario: str,
        stress: Decimal,
        start: pd.Timestamp | None,
        end: pd.Timestamp,
    ):
        return StrategyBacktestEngine(
            self.project_root,
            strategy_config=config,
            account_config=account_config,
            drawdown_control=drawdown_control,
        ).run(
            daily=daily,
            actions=actions,
            calendar=calendar,
            benchmark=benchmark,
            signal_bundle=bundle,
            rule_book=rule_book,
            cost_model=cost_model,
            scenario=scenario,
            stress_multiplier=stress,
            with_costs=True,
            start_date=start,
            end_date=end,
        )

    def _write_report(
        self,
        payload: dict[str, object],
        summary: pd.DataFrame,
    ) -> None:
        path = self.project_root / "runs" / "asset_class_optimization_v1" / "report.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        display = summary[
            [
                "candidate",
                "period",
                "stress_multiplier",
                "total_return",
                "annualized_return",
                "max_drawdown",
                "sharpe_ratio",
                "excess_return",
                "annualized_one_way_turnover",
            ]
        ]
        table = _markdown_table(display)
        selected = payload["selected_candidate"] or "无"
        final_status = "通过" if payload["final_oos_pass"] else "未通过"
        content = f"""# 境内ETF多资产策略优化实验

## 实验原则

- 训练期截至2018-12-31。
- 内部验证期为2019-01-01至2023-10-29。
- 仅在验证期通过后，才运行一次最终样本外。
- 使用固定候选，不在最终样本外继续调参。
- 所有交易继续执行T+1、整手、涨跌停、成本和容量约束。

## 候选方案

- 冻结v1原实现。
- 冻结v1并真正启用已配置的回撤控制。
- 三组资产类别风险预算：保守、均衡、进取。
- 宽基核心加卫星资产配置，并使用60/120/240日趋势共识。
- 资产类别候选在股票、债券、黄金和货币ETF之间分配预算。

## 结果

{table}

## 晋级结论

- 通过内部验证和成本压力的候选：{", ".join(payload["eligible_candidates"]) or "无"}
- 按验证期Sharpe选出的候选：{selected}
- 最终样本外及成本压力：{final_status}

最终样本外结果只用于冻结后检验，不据此继续调参。
"""
        path.write_text(content, encoding="utf-8")


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

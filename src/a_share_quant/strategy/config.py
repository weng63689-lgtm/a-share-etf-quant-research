"""Configuration for the first ETF rotation baseline."""

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class StrategyConfig:
    momentum_days: int
    trend_days: int
    volatility_days: int
    selection_count: int
    retention_rank: int
    target_exposure: float
    max_single_weight: float
    min_trade_weight: float
    min_rebalance_turnover: float
    max_rebalance_turnover: float
    drawdown_reduce_1: float
    drawdown_reduce_2: float
    drawdown_stop: float
    drawdown_level_1_exposure: float
    drawdown_level_2_exposure: float
    drawdown_cooldown_days: int
    drawdown_reentry_exposure: float
    momentum_windows: tuple[int, ...] = (120,)
    trend_windows: tuple[int, ...] = (200,)
    trend_consensus_ratio: float = 0.5
    rebalance_frequency: str = "weekly"
    market_filter: bool = False
    benchmark_trend_days: int = 200
    defensive_exposure: float = 0.50
    portfolio_method: str = "inverse_volatility"
    target_volatility: float = 0.15
    covariance_days: int = 60
    position_stop_loss_pct: float | None = None
    trailing_stop_pct: float | None = None
    trailing_activation_pct: float = 0.0


def load_strategy_config(
    project_root: Path,
    *,
    override_file: Path | None = None,
) -> StrategyConfig:
    payload = yaml.safe_load(
        (project_root / "configs" / "default.yaml").read_text(encoding="utf-8")
    )
    if override_file is not None:
        override = yaml.safe_load(override_file.read_text(encoding="utf-8"))
        if isinstance(override, dict) and isinstance(override.get("strategy"), dict):
            payload.setdefault("strategy", {}).update(override["strategy"])
    strategy = payload.get("strategy", {}) if isinstance(payload, dict) else {}
    risk = payload.get("risk", {}) if isinstance(payload, dict) else {}
    momentum_windows = tuple(
        int(value)
        for value in strategy.get(
            "momentum_windows",
            [strategy.get("momentum_days", 120)],
        )
    )
    trend_windows = tuple(
        int(value) for value in strategy.get("trend_windows", [strategy.get("trend_days", 200)])
    )
    config = StrategyConfig(
        momentum_days=int(strategy.get("momentum_days", 120)),
        trend_days=int(strategy.get("trend_days", 200)),
        volatility_days=int(strategy.get("volatility_days", 20)),
        selection_count=int(strategy.get("selection_count", 3)),
        retention_rank=int(strategy.get("retention_rank", 5)),
        target_exposure=float(strategy.get("target_exposure", 0.95)),
        max_single_weight=float(
            strategy.get("max_single_weight", risk.get("max_single_etf_weight", 0.50))
        ),
        min_trade_weight=float(strategy.get("min_trade_weight", 0.03)),
        min_rebalance_turnover=float(strategy.get("min_rebalance_turnover", 0.05)),
        max_rebalance_turnover=float(strategy.get("max_rebalance_turnover", 0.50)),
        drawdown_reduce_1=float(strategy.get("drawdown_reduce_1", 0.08)),
        drawdown_reduce_2=float(strategy.get("drawdown_reduce_2", 0.12)),
        drawdown_stop=float(strategy.get("drawdown_stop", 0.16)),
        drawdown_level_1_exposure=float(
            strategy.get("drawdown_level_1_exposure", 0.70)
        ),
        drawdown_level_2_exposure=float(
            strategy.get("drawdown_level_2_exposure", 0.40)
        ),
        drawdown_cooldown_days=int(strategy.get("drawdown_cooldown_days", 20)),
        drawdown_reentry_exposure=float(strategy.get("drawdown_reentry_exposure", 0.40)),
        momentum_windows=momentum_windows,
        trend_windows=trend_windows,
        trend_consensus_ratio=float(strategy.get("trend_consensus_ratio", 0.50)),
        rebalance_frequency=str(strategy.get("rebalance_frequency", "weekly")),
        market_filter=bool(strategy.get("market_filter", False)),
        benchmark_trend_days=int(strategy.get("benchmark_trend_days", 200)),
        defensive_exposure=float(strategy.get("defensive_exposure", 0.50)),
        portfolio_method=str(strategy.get("portfolio_method", "inverse_volatility")),
        target_volatility=float(strategy.get("target_volatility", 0.15)),
        covariance_days=int(strategy.get("covariance_days", 60)),
        position_stop_loss_pct=(
            float(strategy["position_stop_loss_pct"])
            if strategy.get("position_stop_loss_pct") is not None
            else None
        ),
        trailing_stop_pct=(
            float(strategy["trailing_stop_pct"])
            if strategy.get("trailing_stop_pct") is not None
            else None
        ),
        trailing_activation_pct=float(strategy.get("trailing_activation_pct", 0.0)),
    )
    if config.momentum_days <= 0 or config.trend_days <= 0 or config.volatility_days <= 0:
        raise ValueError("Strategy lookback periods must be positive")
    if not 0 < config.selection_count <= 6:
        raise ValueError("selection_count must be in (1, 6]")
    if config.retention_rank < config.selection_count:
        raise ValueError("retention_rank must be at least selection_count")
    if not 0 < config.target_exposure <= 1:
        raise ValueError("target_exposure must be in (0, 1]")
    if not 0 < config.max_single_weight <= 1:
        raise ValueError("max_single_weight must be in (0, 1]")
    if not 0 <= config.min_trade_weight < config.max_single_weight:
        raise ValueError("min_trade_weight must be below max_single_weight")
    if not 0 <= config.min_rebalance_turnover <= config.max_rebalance_turnover:
        raise ValueError("min_rebalance_turnover must not exceed max_rebalance_turnover")
    if not 0 < config.max_rebalance_turnover <= 1:
        raise ValueError("max_rebalance_turnover must be in (0, 1]")
    if not (
        0
        < config.drawdown_reduce_1
        < config.drawdown_reduce_2
        < config.drawdown_stop
        < 1
    ):
        raise ValueError("Drawdown thresholds must be strictly increasing")
    if config.drawdown_cooldown_days < 1:
        raise ValueError("drawdown_cooldown_days must be positive")
    if not (
        0
        < config.drawdown_reentry_exposure
        <= config.drawdown_level_2_exposure
        <= config.drawdown_level_1_exposure
        <= 1
    ):
        raise ValueError("Drawdown exposure levels must be in descending order")
    if not config.momentum_windows or min(config.momentum_windows) <= 0:
        raise ValueError("momentum_windows must contain positive values")
    if not config.trend_windows or min(config.trend_windows) <= 0:
        raise ValueError("trend_windows must contain positive values")
    if not 0 < config.trend_consensus_ratio <= 1:
        raise ValueError("trend_consensus_ratio must be in (0, 1]")
    if config.rebalance_frequency not in {"weekly", "monthly"}:
        raise ValueError("rebalance_frequency must be weekly or monthly")
    if config.benchmark_trend_days <= 0:
        raise ValueError("benchmark_trend_days must be positive")
    if not 0 <= config.defensive_exposure <= config.target_exposure:
        raise ValueError("defensive_exposure must be between 0 and target_exposure")
    if config.portfolio_method not in {
        "inverse_volatility",
        "equal_weight",
        "volatility_target",
    }:
        raise ValueError("Unsupported portfolio_method")
    if not 0 < config.target_volatility < 1:
        raise ValueError("target_volatility must be in (0, 1)")
    if config.covariance_days <= 1:
        raise ValueError("covariance_days must be greater than one")
    if config.position_stop_loss_pct is not None and not (
        0 < config.position_stop_loss_pct < 1
    ):
        raise ValueError("position_stop_loss_pct must be in (0, 1)")
    if config.trailing_stop_pct is not None and not (0 < config.trailing_stop_pct < 1):
        raise ValueError("trailing_stop_pct must be in (0, 1)")
    if not 0 <= config.trailing_activation_pct < 1:
        raise ValueError("trailing_activation_pct must be in [0, 1)")
    return config

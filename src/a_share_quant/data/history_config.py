"""Configuration for candidate ETF daily-history ingestion and quality checks."""

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml


@dataclass(frozen=True)
class HistoryConfig:
    start_date: date
    min_coverage_ratio: float
    max_missing_trade_days: int
    max_missing_gap_days: int
    max_daily_return_abs: float


def load_history_config(project_root: Path) -> HistoryConfig:
    path = project_root / "configs" / "default.yaml"
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    data = payload.get("data", {}) if isinstance(payload, dict) else {}

    config = HistoryConfig(
        start_date=date.fromisoformat(str(data.get("history_start", "2012-01-01"))),
        min_coverage_ratio=float(data.get("history_min_coverage_ratio", 0.98)),
        max_missing_trade_days=int(data.get("history_max_missing_trade_days", 10)),
        max_missing_gap_days=int(data.get("history_max_missing_gap_days", 10)),
        max_daily_return_abs=float(data.get("history_max_daily_return_abs", 0.22)),
    )
    if not 0 < config.min_coverage_ratio <= 1:
        raise ValueError("history_min_coverage_ratio must be in (0, 1]")
    if config.max_missing_trade_days < 0 or config.max_missing_gap_days < 0:
        raise ValueError("Missing-day thresholds must not be negative")
    if config.max_daily_return_abs <= 0:
        raise ValueError("history_max_daily_return_abs must be positive")
    return config

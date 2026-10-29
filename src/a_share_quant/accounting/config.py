"""Configuration for the account model and buy-and-hold validation."""

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class AccountConfig:
    initial_cash: float
    lot_size: int
    share_adjustment_return_gap: float


def load_account_config(project_root: Path) -> AccountConfig:
    path = project_root / "configs" / "default.yaml"
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    accounting = payload.get("accounting", {}) if isinstance(payload, dict) else {}
    risk = payload.get("risk", {}) if isinstance(payload, dict) else {}
    execution = payload.get("execution", {}) if isinstance(payload, dict) else {}

    config = AccountConfig(
        initial_cash=float(accounting.get("initial_cash", risk.get("capital_cny", 500000))),
        lot_size=int(accounting.get("lot_size", execution.get("order_lot_size", 100))),
        share_adjustment_return_gap=float(
            accounting.get("share_adjustment_return_gap", 0.05)
        ),
    )
    if config.initial_cash <= 0:
        raise ValueError("accounting.initial_cash must be positive")
    if config.lot_size <= 0:
        raise ValueError("accounting.lot_size must be positive")
    if not 0 < config.share_adjustment_return_gap < 0.5:
        raise ValueError("accounting.share_adjustment_return_gap must be in (0, 0.5)")
    return config

"""Durable local storage for paper-trading state and reports."""

import json
import os
from pathlib import Path
from uuid import uuid4

import pandas as pd


class PaperStore:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.paper_dir = project_root / "data" / "paper"
        self.runs_dir = project_root / "runs" / "paper"
        self.paper_dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    @property
    def state_path(self) -> Path:
        return self.paper_dir / "state.json"

    def load_state(self) -> dict[str, object] | None:
        if not self.state_path.is_file():
            return None
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def save_state(self, state: dict[str, object]) -> Path:
        temporary = self.state_path.with_name(f".{self.state_path.name}.{uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(state, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        os.replace(temporary, self.state_path)
        return self.state_path

    def _save_parquet(self, frame: pd.DataFrame, name: str) -> Path:
        path = self.paper_dir / f"{name}.parquet"
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        frame.to_parquet(temporary, index=False)
        os.replace(temporary, path)
        return path

    def save_snapshots(self, snapshots: pd.DataFrame) -> Path:
        return self._save_parquet(snapshots, "snapshots")

    def save_orders(self, orders: pd.DataFrame) -> Path:
        return self._save_parquet(orders, "orders")

    def save_targets(self, targets: pd.DataFrame) -> Path:
        return self._save_parquet(targets, "targets")

    def save_runs(self, runs: pd.DataFrame) -> Path:
        return self._save_parquet(runs, "runs")

    def load_runs(self) -> pd.DataFrame:
        path = self.paper_dir / "runs.parquet"
        if not path.is_file():
            return pd.DataFrame()
        return pd.read_parquet(path)

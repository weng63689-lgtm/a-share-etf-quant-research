"""Read-only status comparison for the two local QuantDinger signal strategies."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlparse

import httpx

TRACKED_STRATEGY_IDS = (1, 2)


def _read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def default_quantdinger_env(project_root: Path) -> Path:
    configured = os.getenv("QUANTDINGER_ENV_FILE")
    if configured:
        return Path(configured)
    return project_root.parent / "QuantDinger" / ".env"


def human_api_base(values: dict[str, str]) -> str:
    backend_bind = values.get("BACKEND_PORT", "127.0.0.1:5000").strip()
    parsed = urlparse(f"http://{backend_bind}")
    port = parsed.port or 5000
    return f"http://127.0.0.1:{port}"


def fetch_statuses(
    *,
    api_base: str,
    username: str,
    password: str,
    timeout: float = 20.0,
) -> list[dict[str, object]]:
    with httpx.Client(base_url=api_base, timeout=timeout) as client:
        login = client.post(
            "/api/auth/login",
            json={"username": username, "password": password},
        )
        login.raise_for_status()
        token = login.json().get("data", {}).get("token")
        if not token:
            raise RuntimeError("QuantDinger administrator login returned no token")
        response = client.get(
            "/api/strategies?limit=20",
            headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        rows = response.json().get("data", [])
    wanted = [
        {
            "id": row.get("id"),
            "name": row.get("strategy_name"),
            "status": row.get("status"),
            "execution_mode": row.get("execution_mode"),
            "current_equity": row.get("current_equity"),
            "total_pnl": row.get("total_pnl"),
            "today_pnl": row.get("today_pnl"),
            "completed_trades": row.get("completed_trades"),
            "win_rate": row.get("win_rate"),
        }
        for row in rows
        if int(row.get("id") or 0) in TRACKED_STRATEGY_IDS
    ]
    return sorted(wanted, key=lambda row: int(row["id"]))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Show read-only status for frozen v1 and stop-loss candidate v2."
    )
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args()
    project_root = Path(__file__).resolve().parents[3]
    env_path = args.env_file or default_quantdinger_env(project_root)
    values = _read_dotenv(env_path)
    username = values.get("ADMIN_USER")
    password = values.get("ADMIN_PASSWORD")
    if not username or not password:
        raise SystemExit(
            "QuantDinger administrator credentials are missing from "
            f"{env_path}."
        )
    rows = fetch_statuses(
        api_base=human_api_base(values),
        username=username,
        password=password,
    )
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

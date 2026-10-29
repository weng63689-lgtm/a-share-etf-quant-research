"""Command-line environment check for beginners and automated validation."""

import json
import platform
import sys

from a_share_quant import __version__
from a_share_quant.config import get_settings


def main() -> None:
    settings = get_settings()
    required_dirs = [
        settings.data_dir / "raw",
        settings.data_dir / "processed",
        settings.data_dir / "reference",
        settings.project_root / "runs",
        settings.project_root / "logs",
    ]
    missing = [str(path) for path in required_dirs if not path.is_dir()]
    report = {
        "status": "ok" if not missing else "incomplete",
        "package_version": __version__,
        "python_version": platform.python_version(),
        "python_executable": sys.executable,
        "settings": settings.safe_summary(),
        "missing_directories": missing,
        "live_trading_enabled": False,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if missing:
        raise SystemExit(1)


if __name__ == "__main__":
    main()


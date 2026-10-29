from pathlib import Path


def test_required_project_directories_exist() -> None:
    root = Path(__file__).resolve().parents[1]
    expected = [
        "configs",
        "data/raw",
        "data/processed",
        "data/reference",
        "docs",
        "logs",
        "runs",
        "src/a_share_quant",
        "tests",
    ]
    missing = [path for path in expected if not (root / path).is_dir()]
    assert not missing, f"Missing project directories: {missing}"


def test_env_file_is_not_tracked_configuration() -> None:
    root = Path(__file__).resolve().parents[1]
    gitignore = (root / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in gitignore
    assert (root / ".env.example").is_file()


from a_share_quant.config import Settings


def test_safe_summary_never_contains_secret_value() -> None:
    secret = "do-not-print-this-secret"
    settings = Settings(
        data_api_base_url="https://example.test",
        data_api_key=secret,
    )

    summary = str(settings.safe_summary())

    assert secret not in summary
    assert "data_api_configured" in summary


def test_live_project_root_contains_pyproject() -> None:
    settings = Settings()
    assert (settings.project_root / "pyproject.toml").is_file()


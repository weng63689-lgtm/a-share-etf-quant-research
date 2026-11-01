import pytest

from a_share_quant.data.providers.a_stock_data_provider import AStockDataProvider


def test_a_stock_data_provider_maps_tencent_kline_without_amount() -> None:
    rows = [
        ["2026-10-29", "4.145", "4.111", "4.146", "4.103", "21619193.000"],
        ["2026-10-30", "4.059", "4.065", "4.126", "4.046", "21130014.000"],
    ]

    frame = AStockDataProvider._provider_frame(rows)

    assert frame["日期"].tolist() == ["2026-10-29", "2026-10-30"]
    assert frame["成交额"].isna().all()
    assert frame.iloc[-1]["收盘"] == pytest.approx(4.065)
    assert frame.iloc[-1]["涨跌额"] == pytest.approx(-0.046)


def test_a_stock_data_provider_rejects_duplicate_dates() -> None:
    rows = [
        ["2026-10-30", "4.059", "4.065", "4.126", "4.046", "21130014.000"],
        ["2026-10-30", "4.059", "4.065", "4.126", "4.046", "21130014.000"],
    ]

    with pytest.raises(RuntimeError, match="duplicate date"):
        AStockDataProvider._provider_frame(rows)

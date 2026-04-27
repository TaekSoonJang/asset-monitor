from decimal import Decimal

from asset_monitor.brokers.upbit.collector import build_upbit_records


def test_build_upbit_records_filters_small_assets_and_uses_locked_quantity() -> None:
    records = build_upbit_records(
        [
            {"currency": "KRW", "balance": "9000", "locked": "0"},
            {"currency": "BTC", "balance": "0.001", "locked": "0.0001"},
            {"currency": "ETH", "balance": "0.001", "locked": "0"},
            {"currency": "DOGE", "balance": "100", "locked": "0"},
        ],
        {"BTC": Decimal("100000000"), "ETH": Decimal("3000000")},
        captured_at="2026-04-27T12:00:00+09:00",
        owner_name="sunha",
        account_name="Upbit",
        min_amount_krw=Decimal("10000"),
    )

    assert records["domestic"] == []
    assert records["cash"] == []
    assert len(records["foreign"]) == 1
    assert records["foreign"][0].broker_name == "upbit"
    assert records["foreign"][0].asset_group == "crypto_asset"
    assert records["foreign"][0].asset_subtype == ""
    assert records["foreign"][0].market == "Upbit"
    assert records["foreign"][0].symbol == "BTC"
    assert records["foreign"][0].quantity == Decimal("0.0011")
    assert records["foreign"][0].amount_in_krw == Decimal("110000.0000")


def test_build_upbit_records_includes_krw_cash_above_minimum() -> None:
    records = build_upbit_records(
        [{"currency": "KRW", "balance": "12000", "locked": "500"}],
        {},
        captured_at="2026-04-27T12:00:00+09:00",
        owner_name="sunha",
        account_name="Upbit",
        min_amount_krw=Decimal("10000"),
    )

    assert len(records["cash"]) == 1
    assert records["cash"][0].asset_group == "cash_equivalent"
    assert records["cash"][0].asset_subtype == "krw_cash"
    assert records["cash"][0].market == "Upbit"
    assert records["cash"][0].amount_in_krw == Decimal("12500")

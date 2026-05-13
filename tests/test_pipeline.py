from asset_monitor.models import AssetRecord
from asset_monitor.pipeline import _canonicalize_records


def _record(*, symbol: str, name: str, asset_group: str = "foreign_stock") -> AssetRecord:
    return AssetRecord(
        captured_at="2026-05-13T20:47:52+09:00",
        broker_name="miraeasset",
        owner_name="owner",
        account_name="",
        account_masked_id="",
        asset_group=asset_group,
        asset_subtype="retirement_pension",
        market="",
        symbol=symbol,
        name=name,
        quantity=None,
        unit_currency="KRW",
        amount_in_unit_currency=None,
        fx_rate_to_krw=None,
        amount_in_krw=None,
        source_page="miraeasset_retirement_pension_holdings",
    )


def test_canonicalize_records_replaces_internal_product_symbol_by_name() -> None:
    records = [
        _record(symbol="A133690", name="TIGER 미국나스닥100"),
        _record(symbol="212001000229", name="TIGER 미국나스닥100"),
    ]

    _canonicalize_records(records)

    assert records[1].symbol == "A133690"


def test_canonicalize_records_does_not_rewrite_cash_internal_symbols() -> None:
    records = [
        _record(symbol="A133690", name="TIGER 미국나스닥100"),
        _record(symbol="910001000001", name="TIGER 미국나스닥100", asset_group="cash_equivalent"),
    ]

    _canonicalize_records(records)

    assert records[1].symbol == "910001000001"

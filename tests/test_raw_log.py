from decimal import Decimal

from asset_monitor.models import AssetRecord
from asset_monitor.raw_log import LocalSnapshotLogger


def _record(
    *,
    captured_at: str,
    broker_name: str = "shinhan",
    owner_name: str = "sunha",
    symbol: str = "005930",
    amount: str = "1000",
) -> AssetRecord:
    return AssetRecord(
        captured_at=captured_at,
        broker_name=broker_name,
        owner_name=owner_name,
        account_name="main",
        account_masked_id="",
        asset_group="domestic_stock",
        asset_subtype="stock",
        market="KRX",
        symbol=symbol,
        name=symbol,
        quantity=Decimal("1"),
        unit_currency="KRW",
        amount_in_unit_currency=Decimal(amount),
        fx_rate_to_krw=None,
        amount_in_krw=Decimal(amount),
        source_page="test",
    )


def test_latest_records_for_accounts_returns_latest_before_current_run(tmp_path) -> None:
    logger = LocalSnapshotLogger(tmp_path)
    logger.append(
        "2026-05-13T10:00:00+09:00",
        [
            _record(captured_at="2026-05-13T10:00:00+09:00"),
            _record(captured_at="2026-05-13T10:00:00+09:00", symbol="OLD", amount="500"),
        ],
    )
    logger.append("2026-05-13T11:00:00+09:00", [_record(captured_at="2026-05-13T11:00:00+09:00", amount="2000")])
    logger.append("2026-05-13T12:00:00+09:00", [_record(captured_at="2026-05-13T12:00:00+09:00", amount="3000")])

    records = logger.latest_records_for_accounts(
        {("shinhan", "sunha")},
        before_captured_at="2026-05-13T12:00:00+09:00",
    )

    assert len(records) == 1
    assert records[0].captured_at == "2026-05-13T11:00:00+09:00"
    assert records[0].symbol == "005930"
    assert records[0].amount_in_krw == Decimal("2000")


def test_latest_records_for_accounts_filters_other_accounts(tmp_path) -> None:
    logger = LocalSnapshotLogger(tmp_path)
    logger.append(
        "2026-05-13T10:00:00+09:00",
        [
            _record(captured_at="2026-05-13T10:00:00+09:00"),
            _record(
                captured_at="2026-05-13T10:00:00+09:00",
                broker_name="upbit",
                owner_name="sunha",
                symbol="BTC",
            ),
        ],
    )

    records = logger.latest_records_for_accounts({("upbit", "sunha")})

    assert len(records) == 1
    assert records[0].broker_name == "upbit"
    assert records[0].symbol == "BTC"


def test_latest_records_for_accounts_skips_legacy_malformed_rows(tmp_path) -> None:
    logger = LocalSnapshotLogger(tmp_path)
    logger.append("2026-05-13T10:00:00+09:00", [_record(captured_at="2026-05-13T10:00:00+09:00")])
    log_path = tmp_path / "raw_snapshots-2026-05-13.jsonl"
    log_path.write_text(
        '{"captured_at": "2026-05-13T09:00:00+09:00", "symbol": "OLD"}\n' + log_path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    records = logger.latest_records_for_accounts({("shinhan", "sunha")})

    assert len(records) == 1
    assert records[0].symbol == "005930"

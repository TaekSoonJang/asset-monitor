from decimal import Decimal

from asset_monitor.models import AssetRecord, RunLogEntry
from asset_monitor.sheets import (
    ADDITIONAL_ASSET_SHEET,
    DAILY_TREND_HEADERS,
    DAILY_TREND_SHEET,
    GoogleSheetsWriter,
    LATEST_ASSET_SHEET,
    RUN_LOG_HEADERS,
    RUN_LOG_SHEET,
)


class FakeRequest:
    def __init__(self, response: dict | None = None) -> None:
        self.response = response or {}

    def execute(self) -> dict:
        return self.response


class FakeValuesService:
    def __init__(self) -> None:
        self.ranges: dict[str, list[list[str]]] = {}
        self.updated: dict[str, list[list[str]]] = {}
        self.cleared: list[str] = []
        self.appended: dict[str, list[list[str]]] = {}

    def get(self, **kwargs) -> FakeRequest:
        return FakeRequest({"values": self.ranges.get(kwargs["range"], [])})

    def update(self, **kwargs) -> FakeRequest:
        self.updated[kwargs["range"]] = kwargs["body"]["values"]
        return FakeRequest()

    def append(self, **kwargs) -> FakeRequest:
        rows = kwargs["body"]["values"]
        self.appended.setdefault(kwargs["range"], []).extend(rows)
        sheet_name = kwargs["range"].split("!", maxsplit=1)[0]
        column_count = len(rows[0]) if rows else 1
        last_column = chr(ord("A") + column_count - 1)
        data_range = f"{sheet_name}!A2:{last_column}"
        self.ranges.setdefault(data_range, []).extend(rows)
        return FakeRequest()

    def clear(self, **kwargs) -> FakeRequest:
        self.cleared.append(kwargs["range"])
        return FakeRequest()


class FakeSheetsService:
    def __init__(self) -> None:
        self.batch_update_body: dict | None = None
        self.values_service = FakeValuesService()

    def spreadsheets(self) -> "FakeSheetsService":
        return self

    def get(self, **kwargs) -> FakeRequest:
        return FakeRequest(
            {
                "sheets": [
                    {"properties": {"title": "최신자산", "sheetId": 1}},
                    {"properties": {"title": "추가 금융자산", "sheetId": 4}},
                    {"properties": {"title": "자산요약", "sheetId": 2}},
                    {"properties": {"title": "실행로그", "sheetId": 3}},
                ]
            }
        )

    def batchUpdate(self, **kwargs) -> FakeRequest:
        self.batch_update_body = kwargs["body"]
        return FakeRequest()

    def values(self) -> FakeValuesService:
        return self.values_service


def test_ensure_tabs_renames_latest_asset_sheet_to_financial_asset() -> None:
    service = FakeSheetsService()
    writer = object.__new__(GoogleSheetsWriter)
    writer.spreadsheet_id = "spreadsheet-id"
    writer.service = service

    writer.ensure_tabs()

    assert LATEST_ASSET_SHEET == "금융자산"
    assert service.batch_update_body == {
        "requests": [
            {
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": 1,
                        "title": "금융자산",
                    },
                    "fields": "title",
                }
            },
            {"addSheet": {"properties": {"title": DAILY_TREND_SHEET}}},
        ]
    }
    assert service.values_service.updated[f"{DAILY_TREND_SHEET}!1:1"] == [DAILY_TREND_HEADERS]


def test_append_run_log_keeps_only_latest_20_entries() -> None:
    service = FakeSheetsService()
    service.values_service.ranges[f"{RUN_LOG_SHEET}!A2:J"] = [
        [f"2026-04-28T{i:02}:00:00+09:00"] + [str(i)] * 9 for i in range(20)
    ]

    writer = object.__new__(GoogleSheetsWriter)
    writer.spreadsheet_id = "spreadsheet-id"
    writer.service = service

    writer.append_run_log(
        RunLogEntry(
            captured_at="2026-04-28T20:00:00+09:00",
            broker_name="shinhan",
            owner_name="sunha",
            status="success",
            total_records=1,
            domestic_records=1,
            foreign_records=0,
            cash_records=0,
            message="ok",
            debug_dir="logs/debug",
        )
    )

    updated_rows = service.values_service.updated[f"{RUN_LOG_SHEET}!A1"]

    assert service.values_service.cleared == [RUN_LOG_SHEET]
    assert updated_rows[0] == RUN_LOG_HEADERS
    assert len(updated_rows) == 21
    assert updated_rows[1][0] == "2026-04-28T01:00:00+09:00"
    assert updated_rows[-1][0] == "2026-04-28T20:00:00+09:00"


def _asset_record(
    *,
    asset_group: str,
    amount: str,
    asset_subtype: str = "stock",
    captured_at: str = "2026-04-28T07:00:00+09:00",
    symbol: str = "",
) -> AssetRecord:
    return AssetRecord(
        captured_at=captured_at,
        broker_name="shinhan",
        owner_name="sunha",
        account_name="",
        account_masked_id="",
        asset_group=asset_group,
        asset_subtype=asset_subtype,
        market="KRX",
        symbol=symbol,
        name=symbol or f"{asset_group}-{asset_subtype}",
        quantity=Decimal("1"),
        unit_currency="KRW",
        amount_in_unit_currency=Decimal(amount),
        fx_rate_to_krw=None,
        amount_in_krw=Decimal(amount),
        source_page="test",
    )


def test_append_daily_trend_skips_before_7am() -> None:
    service = FakeSheetsService()
    writer = object.__new__(GoogleSheetsWriter)
    writer.spreadsheet_id = "spreadsheet-id"
    writer.service = service

    appended = writer.append_daily_trend(
        [_asset_record(asset_group="domestic_stock", amount="1000")],
        captured_at="2026-04-28T06:59:59+09:00",
        timezone="Asia/Seoul",
    )

    assert appended is False
    assert service.values_service.appended == {}


def test_append_daily_trend_appends_totals_after_7am() -> None:
    service = FakeSheetsService()
    writer = object.__new__(GoogleSheetsWriter)
    writer.spreadsheet_id = "spreadsheet-id"
    writer.service = service

    appended = writer.append_daily_trend(
        [
            _asset_record(asset_group="domestic_stock", amount="1000"),
            _asset_record(asset_group="foreign_stock", amount="2000"),
            _asset_record(asset_group="cash_equivalent", amount="3000"),
            _asset_record(asset_group="crypto_asset", amount="4000"),
            _asset_record(asset_group="foreign_stock", asset_subtype="personal_pension", amount="500"),
            _asset_record(asset_group="cash_equivalent", asset_subtype="retirement_pension", amount="600"),
        ],
        captured_at="2026-04-28T07:00:00+09:00",
        timezone="Asia/Seoul",
    )

    row = service.values_service.appended[f"{DAILY_TREND_SHEET}!A:A"][0]

    assert appended is True
    assert row == [
        "2026-04-28",
        "2026-04-28T07:00:00+09:00",
        "1000",
        "2500",
        "3600",
        "4000",
        "11100",
        "10000",
        "1100",
    ]


def test_append_daily_trend_skips_existing_date() -> None:
    service = FakeSheetsService()
    service.values_service.ranges[f"{DAILY_TREND_SHEET}!A2:A"] = [["2026-04-28"]]
    writer = object.__new__(GoogleSheetsWriter)
    writer.spreadsheet_id = "spreadsheet-id"
    writer.service = service

    appended = writer.append_daily_trend(
        [_asset_record(asset_group="domestic_stock", amount="1000")],
        captured_at="2026-04-28T07:00:00+09:00",
        timezone="Asia/Seoul",
    )

    assert appended is False
    assert service.values_service.appended == {}


def test_append_daily_trend_dedupes_to_latest_record() -> None:
    service = FakeSheetsService()
    writer = object.__new__(GoogleSheetsWriter)
    writer.spreadsheet_id = "spreadsheet-id"
    writer.service = service

    writer.append_daily_trend(
        [
            _asset_record(
                asset_group="domestic_stock",
                amount="1000",
                captured_at="2026-04-28T07:00:00+09:00",
                symbol="005930",
            ),
            _asset_record(
                asset_group="domestic_stock",
                amount="1500",
                captured_at="2026-04-28T07:05:00+09:00",
                symbol="005930",
            ),
        ],
        captured_at="2026-04-28T07:05:00+09:00",
        timezone="Asia/Seoul",
    )

    row = service.values_service.appended[f"{DAILY_TREND_SHEET}!A:A"][0]

    assert row[2] == "1500"
    assert row[6] == "1500"


def test_append_daily_trend_includes_additional_assets() -> None:
    service = FakeSheetsService()
    service.values_service.ranges[f"{ADDITIONAL_ASSET_SHEET}!A2:L"] = [
        [
            "2026-04-28",
            "MorganStanley",
            "sunha",
            "해외주식",
            "주식",
            "미국",
            "GOOGL",
            "GOOGL",
            "80.16",
            "USD",
            "1469",
            "44,888,688",
        ],
        [
            "2026-04-28",
            "Manual",
            "sunha",
            "현금성자산",
            "개인연금",
            "",
            "",
            "Pension cash",
            "",
            "KRW",
            "",
            "1,000",
        ],
    ]
    writer = object.__new__(GoogleSheetsWriter)
    writer.spreadsheet_id = "spreadsheet-id"
    writer.service = service

    writer.append_daily_trend(
        [_asset_record(asset_group="foreign_stock", amount="2000")],
        captured_at="2026-04-28T07:00:00+09:00",
        timezone="Asia/Seoul",
    )

    row = service.values_service.appended[f"{DAILY_TREND_SHEET}!A:A"][0]

    assert row[3] == "44890688"
    assert row[4] == "1000"
    assert row[6] == "44891688"
    assert row[7] == "44890688"
    assert row[8] == "1000"


def test_refresh_latest_views_updates_latest_assets_only() -> None:
    service = FakeSheetsService()

    writer = object.__new__(GoogleSheetsWriter)
    writer.spreadsheet_id = "spreadsheet-id"
    writer.service = service

    writer.refresh_latest_views(
        [
            AssetRecord(
                captured_at="2026-04-28T10:00:00+09:00",
                broker_name="shinhan",
                owner_name="sunha",
                account_name="",
                account_masked_id="",
                asset_group="domestic_stock",
                asset_subtype="stock",
                market="KRX",
                symbol="005930",
                name="삼성전자",
                quantity=Decimal("1"),
                unit_currency="KRW",
                amount_in_unit_currency=Decimal("1000"),
                fx_rate_to_krw=None,
                amount_in_krw=Decimal("1000"),
                source_page="domestic",
            )
        ]
    )

    latest_rows = service.values_service.updated["금융자산!A1"]

    assert len(latest_rows) == 2
    assert latest_rows[1][7] == "삼성전자"
    assert "자산요약" not in service.values_service.cleared
    assert "자산요약!A1" not in service.values_service.updated

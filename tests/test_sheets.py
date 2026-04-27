from decimal import Decimal

from asset_monitor.models import AssetRecord
from asset_monitor.sheets import GoogleSheetsWriter, LATEST_ASSET_SHEET


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

    def get(self, **kwargs) -> FakeRequest:
        return FakeRequest({"values": self.ranges.get(kwargs["range"], [])})

    def update(self, **kwargs) -> FakeRequest:
        self.updated[kwargs["range"]] = kwargs["body"]["values"]
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
            }
        ]
    }


def test_refresh_latest_views_summarizes_auto_and_additional_assets() -> None:
    service = FakeSheetsService()
    service.values_service.ranges["추가 금융자산!A2:L"] = [
        [
            "2026-04-28T09:00:00+09:00",
            "수동",
            "sunha",
            "현금성자산",
            "원화예수금",
            "",
            "",
            "추가 예금",
            "",
            "KRW",
            "",
            "2000",
        ]
    ]

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
    summary_rows = service.values_service.updated["자산요약!A1"]

    assert len(latest_rows) == 2
    assert latest_rows[1][7] == "삼성전자"
    assert any(row == ["sunha 국내주식 합계(원화환산)", "1000"] for row in summary_rows)
    assert any(row == ["sunha 현금성자산 합계(원화환산)", "2000"] for row in summary_rows)
    assert any(row == ["sunha 총자산(원화환산)", "3000"] for row in summary_rows)

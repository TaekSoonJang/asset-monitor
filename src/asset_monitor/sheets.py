from __future__ import annotations

from decimal import Decimal, InvalidOperation

from google.oauth2.service_account import Credentials
from googleapiclient.errors import HttpError
from googleapiclient.discovery import build

from .models import AssetRecord, RunLogEntry
from .parsing import summarize_latest

LATEST_ASSET_SHEET = "금융자산"
ADDITIONAL_ASSET_SHEET = "추가 금융자산"
SUMMARY_SHEET = "자산요약"
RUN_LOG_SHEET = "실행로그"
LEGACY_RAW_SHEET = "원본스냅샷보관"

LEGACY_SHEET_NAMES = {
    "latest_by_asset": LATEST_ASSET_SHEET,
    "최신자산": LATEST_ASSET_SHEET,
    "latest_summary": SUMMARY_SHEET,
    "run_log": RUN_LOG_SHEET,
    "raw_snapshots": LEGACY_RAW_SHEET,
}

LATEST_HEADERS = [
    "수집시각",
    "금융기관",
    "소유자",
    "자산구분",
    "자산세부구분",
    "시장",
    "종목코드",
    "종목명",
    "수량",
    "통화",
    "환율(원화환산)",
    "원화환산금액",
]

RUN_LOG_HEADERS = [
    "수집시각",
    "금융기관",
    "소유자",
    "상태",
    "총건수",
    "국내주식건수",
    "해외주식건수",
    "현금성자산건수",
    "메시지",
    "디버그경로",
]

BROKER_CODES_BY_LABEL = {
    "신한투자증권": "shinhan",
    "미래에셋증권": "miraeasset",
    "키움증권": "kiwoom",
    "Upbit": "upbit",
}

ASSET_GROUP_CODES_BY_LABEL = {
    "국내주식": "domestic_stock",
    "해외주식": "foreign_stock",
    "현금성자산": "cash_equivalent",
    "보유상품": "broker_position",
    "코인": "crypto_asset",
}

ASSET_SUBTYPE_CODES_BY_LABEL = {
    "주식": "stock",
    "원화예수금": "krw_cash",
    "외화예수금": "fx_cash",
    "RP": "rp",
    "개인연금": "personal_pension",
    "퇴직연금": "retirement_pension",
}


class GoogleSheetsWriter:
    def __init__(self, service_account_info: dict, spreadsheet_id: str) -> None:
        credentials = Credentials.from_service_account_info(
            service_account_info,
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        self.spreadsheet_id = spreadsheet_id
        self.service = build("sheets", "v4", credentials=credentials, cache_discovery=False)

    def ensure_tabs(self) -> None:
        spreadsheet = self.service.spreadsheets().get(spreadsheetId=self.spreadsheet_id).execute()
        sheets = spreadsheet.get("sheets", [])
        existing = {sheet["properties"]["title"]: sheet["properties"]["sheetId"] for sheet in sheets}
        normalized_existing = set(existing)

        requests: list[dict] = []
        for old_name, new_name in LEGACY_SHEET_NAMES.items():
            if old_name in existing and new_name not in existing:
                requests.append(
                    {
                        "updateSheetProperties": {
                            "properties": {
                                "sheetId": existing[old_name],
                                "title": new_name,
                            },
                            "fields": "title",
                        }
                    }
                )
                normalized_existing.discard(old_name)
                normalized_existing.add(new_name)

        for title in (LATEST_ASSET_SHEET, ADDITIONAL_ASSET_SHEET, SUMMARY_SHEET, RUN_LOG_SHEET):
            if title not in normalized_existing:
                requests.append({"addSheet": {"properties": {"title": title}}})

        if requests:
            self.service.spreadsheets().batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body={"requests": requests},
            ).execute()

        self._sync_header(LATEST_ASSET_SHEET, LATEST_HEADERS)
        self._sync_header(ADDITIONAL_ASSET_SHEET, LATEST_HEADERS)
        self._sync_header(SUMMARY_SHEET, ["구분", "값"])
        self._sync_header(RUN_LOG_SHEET, RUN_LOG_HEADERS)

    def refresh_latest_views(self, records: list[AssetRecord]) -> None:
        latest_rows, _ = summarize_latest(records)
        latest_payload = [LATEST_HEADERS] + latest_rows
        self._replace_sheet(LATEST_ASSET_SHEET, latest_payload)

        additional_records = self._read_additional_asset_records()
        _, summary_rows = summarize_latest(records + additional_records)
        self._replace_sheet(SUMMARY_SHEET, summary_rows)

    def append_run_log(self, entry: RunLogEntry) -> None:
        self.service.spreadsheets().values().append(
            spreadsheetId=self.spreadsheet_id,
            range=f"{RUN_LOG_SHEET}!A:A",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [entry.to_sheet_row()]},
        ).execute()

    def _sync_header(self, sheet_name: str, header: list[str]) -> None:
        existing = self.service.spreadsheets().values().get(
            spreadsheetId=self.spreadsheet_id,
            range=f"{sheet_name}!1:1",
        ).execute()
        values = existing.get("values", [])
        if values and values[0] == header:
            return
        self.service.spreadsheets().values().update(
            spreadsheetId=self.spreadsheet_id,
            range=f"{sheet_name}!1:1",
            valueInputOption="RAW",
            body={"values": [header]},
        ).execute()

    def _replace_sheet(self, sheet_name: str, values: list[list[str]]) -> None:
        self.service.spreadsheets().values().clear(
            spreadsheetId=self.spreadsheet_id,
            range=sheet_name,
            body={},
        ).execute()
        self.service.spreadsheets().values().update(
            spreadsheetId=self.spreadsheet_id,
            range=f"{sheet_name}!A1",
            valueInputOption="USER_ENTERED",
            body={"values": values},
        ).execute()

    def _read_additional_asset_records(self) -> list[AssetRecord]:
        try:
            response = self.service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,
                range=f"{ADDITIONAL_ASSET_SHEET}!A2:L",
            ).execute()
        except HttpError as exc:
            if exc.resp.status == 400:
                return []
            raise

        return [_asset_record_from_sheet_row(row) for row in response.get("values", []) if _has_asset_row_value(row)]


def build_run_log_message(errors: dict[str, str]) -> str:
    if not errors:
        return "성공"
    return "; ".join(f"{key}={value}" for key, value in sorted(errors.items()))


def _asset_record_from_sheet_row(row: list[str]) -> AssetRecord:
    padded = row + [""] * (len(LATEST_HEADERS) - len(row))
    return AssetRecord(
        captured_at=padded[0],
        broker_name=BROKER_CODES_BY_LABEL.get(padded[1], padded[1]),
        owner_name=padded[2],
        account_name="",
        account_masked_id="",
        asset_group=ASSET_GROUP_CODES_BY_LABEL.get(padded[3], padded[3]),
        asset_subtype=ASSET_SUBTYPE_CODES_BY_LABEL.get(padded[4], padded[4]),
        market=padded[5],
        symbol=padded[6],
        name=padded[7],
        quantity=_parse_decimal(padded[8]),
        unit_currency=padded[9] or "KRW",
        fx_rate_to_krw=_parse_decimal(padded[10]),
        amount_in_unit_currency=None,
        amount_in_krw=_parse_decimal(padded[11]),
        source_page="manual_additional_asset",
    )


def _has_asset_row_value(row: list[str]) -> bool:
    return any(cell.strip() for cell in row)


def _parse_decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    cleaned = str(value).strip().replace(",", "")
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None

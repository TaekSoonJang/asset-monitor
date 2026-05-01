from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from .models import AssetRecord, RunLogEntry
from .parsing import parse_decimal, summarize_latest

LATEST_ASSET_SHEET = "금융자산"
ADDITIONAL_ASSET_SHEET = "추가 금융자산"
RUN_LOG_SHEET = "실행로그"
DAILY_TREND_SHEET = "일별 추이"
LEGACY_RAW_SHEET = "원본스냅샷보관"

RUN_LOG_MAX_ENTRIES = 20
DAILY_TREND_UPDATE_HOUR = 7
PENSION_ASSET_SUBTYPES = {"personal_pension", "retirement_pension"}
PENSION_ASSET_SUBTYPE_LABELS = {"개인연금", "퇴직연금"}

LEGACY_SHEET_NAMES = {
    "latest_by_asset": LATEST_ASSET_SHEET,
    "최신자산": LATEST_ASSET_SHEET,
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

DAILY_TREND_HEADERS = [
    "날짜",
    "수집시각",
    "국내주식 합계",
    "해외주식 합계",
    "현금성 합계",
    "코인 합계",
    "전체 자산 합계",
    "비연금성 합계",
    "연금성 합계",
]

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

        for title in (LATEST_ASSET_SHEET, ADDITIONAL_ASSET_SHEET, RUN_LOG_SHEET, DAILY_TREND_SHEET):
            if title not in normalized_existing:
                requests.append({"addSheet": {"properties": {"title": title}}})

        if requests:
            self.service.spreadsheets().batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body={"requests": requests},
            ).execute()

        self._sync_header(LATEST_ASSET_SHEET, LATEST_HEADERS)
        self._sync_header(ADDITIONAL_ASSET_SHEET, LATEST_HEADERS)
        self._sync_header(RUN_LOG_SHEET, RUN_LOG_HEADERS)
        self._sync_header(DAILY_TREND_SHEET, DAILY_TREND_HEADERS)

    def refresh_latest_views(self, records: list[AssetRecord]) -> None:
        latest_rows, _ = summarize_latest(records)
        latest_payload = [LATEST_HEADERS] + latest_rows
        self._replace_sheet(LATEST_ASSET_SHEET, latest_payload)

    def append_daily_trend(
        self,
        records: list[AssetRecord],
        *,
        captured_at: str,
        timezone: str,
    ) -> bool:
        local_captured_at = _to_local_datetime(captured_at, timezone)
        if local_captured_at.hour < DAILY_TREND_UPDATE_HOUR:
            return False

        target_date = local_captured_at.date().isoformat()
        existing = self.service.spreadsheets().values().get(
            spreadsheetId=self.spreadsheet_id,
            range=f"{DAILY_TREND_SHEET}!A2:A",
        ).execute()
        existing_dates = {row[0] for row in existing.get("values", []) if row}
        if target_date in existing_dates:
            return False

        additional_rows = self.service.spreadsheets().values().get(
            spreadsheetId=self.spreadsheet_id,
            range=f"{ADDITIONAL_ASSET_SHEET}!A2:L",
        ).execute()
        additional_totals = _summarize_additional_asset_rows(additional_rows.get("values", []))

        self.service.spreadsheets().values().append(
            spreadsheetId=self.spreadsheet_id,
            range=f"{DAILY_TREND_SHEET}!A:A",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={
                "values": [
                    _build_daily_trend_row(
                        records,
                        additional_totals=additional_totals,
                        target_date=target_date,
                        captured_at=local_captured_at.isoformat(timespec="seconds"),
                    )
                ]
            },
        ).execute()
        return True

    def append_run_log(self, entry: RunLogEntry) -> None:
        self.service.spreadsheets().values().append(
            spreadsheetId=self.spreadsheet_id,
            range=f"{RUN_LOG_SHEET}!A:A",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [entry.to_sheet_row()]},
        ).execute()
        self._trim_run_log()

    def _trim_run_log(self) -> None:
        existing = self.service.spreadsheets().values().get(
            spreadsheetId=self.spreadsheet_id,
            range=f"{RUN_LOG_SHEET}!A2:J",
        ).execute()
        rows = existing.get("values", [])
        if len(rows) <= RUN_LOG_MAX_ENTRIES:
            return
        self._replace_sheet(RUN_LOG_SHEET, [RUN_LOG_HEADERS] + rows[-RUN_LOG_MAX_ENTRIES:])

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


def build_run_log_message(errors: dict[str, str]) -> str:
    if not errors:
        return "성공"
    return "; ".join(f"{key}={value}" for key, value in sorted(errors.items()))


def _to_local_datetime(captured_at: str, timezone: str) -> datetime:
    parsed = datetime.fromisoformat(captured_at)
    local_timezone = ZoneInfo(timezone)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=local_timezone)
    return parsed.astimezone(local_timezone)


def _build_daily_trend_row(
    records: list[AssetRecord],
    *,
    additional_totals: dict[str, Decimal] | None = None,
    target_date: str,
    captured_at: str,
) -> list[str]:
    additional_totals = additional_totals or {}
    latest_records = _latest_records(records)
    domestic_total = _sum_records_by_asset_group(latest_records, "domestic_stock") + additional_totals.get(
        "domestic_stock", Decimal("0")
    )
    foreign_total = _sum_records_by_asset_group(latest_records, "foreign_stock") + additional_totals.get(
        "foreign_stock", Decimal("0")
    )
    cash_total = _sum_records_by_asset_group(latest_records, "cash_equivalent") + additional_totals.get(
        "cash_equivalent", Decimal("0")
    )
    crypto_total = _sum_records_by_asset_group(latest_records, "crypto_asset") + additional_totals.get(
        "crypto_asset", Decimal("0")
    )
    total = (
        sum((record.amount_in_krw or Decimal("0") for record in latest_records), Decimal("0"))
        + additional_totals.get("total", Decimal("0"))
    )
    pension_total = sum(
        (
            record.amount_in_krw or Decimal("0")
            for record in latest_records
            if record.asset_subtype in PENSION_ASSET_SUBTYPES
        ),
        Decimal("0"),
    ) + additional_totals.get("pension", Decimal("0"))
    non_pension_total = total - pension_total

    return [
        target_date,
        captured_at,
        _decimal_to_string(domestic_total),
        _decimal_to_string(foreign_total),
        _decimal_to_string(cash_total),
        _decimal_to_string(crypto_total),
        _decimal_to_string(total),
        _decimal_to_string(non_pension_total),
        _decimal_to_string(pension_total),
    ]


def _latest_records(records: list[AssetRecord]) -> list[AssetRecord]:
    latest: dict[tuple[str, str, str, str, str, str], AssetRecord] = {}
    for record in sorted(records, key=lambda item: item.captured_at):
        latest[record.identity_key()] = record
    return list(latest.values())


def _sum_records_by_asset_group(records: list[AssetRecord], asset_group: str) -> Decimal:
    return sum(
        (record.amount_in_krw or Decimal("0") for record in records if record.asset_group == asset_group),
        Decimal("0"),
    )


def _summarize_additional_asset_rows(rows: list[list[str]]) -> dict[str, Decimal]:
    totals = {
        "domestic_stock": Decimal("0"),
        "foreign_stock": Decimal("0"),
        "cash_equivalent": Decimal("0"),
        "crypto_asset": Decimal("0"),
        "total": Decimal("0"),
        "pension": Decimal("0"),
    }
    for row in rows:
        asset_group = _get_row_value(row, 3)
        asset_subtype = _get_row_value(row, 4)
        amount = parse_decimal(_get_row_value(row, 11)) or Decimal("0")
        if amount == 0:
            continue

        totals["total"] += amount
        group_key = _additional_asset_group_key(asset_group)
        if group_key:
            totals[group_key] += amount
        if asset_subtype in PENSION_ASSET_SUBTYPES or asset_subtype in PENSION_ASSET_SUBTYPE_LABELS:
            totals["pension"] += amount
    return totals


def _additional_asset_group_key(asset_group: str) -> str | None:
    if "국내" in asset_group and "주식" in asset_group:
        return "domestic_stock"
    if "해외" in asset_group and "주식" in asset_group:
        return "foreign_stock"
    if "현금" in asset_group:
        return "cash_equivalent"
    if "코인" in asset_group:
        return "crypto_asset"
    if asset_group in {"domestic_stock", "foreign_stock", "cash_equivalent", "crypto_asset"}:
        return asset_group
    return None


def _get_row_value(row: list[str], index: int) -> str:
    if index >= len(row):
        return ""
    return row[index].strip()


def _decimal_to_string(value: Decimal) -> str:
    return format(value, "f")

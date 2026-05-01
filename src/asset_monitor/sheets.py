from __future__ import annotations

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from .models import AssetRecord, RunLogEntry
from .parsing import summarize_latest

LATEST_ASSET_SHEET = "금융자산"
ADDITIONAL_ASSET_SHEET = "추가 금융자산"
RUN_LOG_SHEET = "실행로그"
LEGACY_RAW_SHEET = "원본스냅샷보관"

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

        for title in (LATEST_ASSET_SHEET, ADDITIONAL_ASSET_SHEET, RUN_LOG_SHEET):
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

    def refresh_latest_views(self, records: list[AssetRecord]) -> None:
        latest_rows, _ = summarize_latest(records)
        latest_payload = [LATEST_HEADERS] + latest_rows
        self._replace_sheet(LATEST_ASSET_SHEET, latest_payload)

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


def build_run_log_message(errors: dict[str, str]) -> str:
    if not errors:
        return "성공"
    return "; ".join(f"{key}={value}" for key, value in sorted(errors.items()))

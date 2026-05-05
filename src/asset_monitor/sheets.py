from __future__ import annotations

from dataclasses import dataclass
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
SECTOR_CLASSIFICATION_SHEET = "섹터분류"
SECTOR_STATUS_SHEET = "섹터별 현황"
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

SECTOR_CLASSIFICATION_HEADERS = [
    "매칭키",
    "종목코드",
    "종목명",
    "섹터",
    "포함여부",
    "고정여부",
    "메모",
]

SECTOR_STATUS_HEADERS = [
    "수집시각",
    "섹터",
    "금액",
    "전체비중",
    "테슬라제외비중",
    "종목수",
    "주요종목",
]

DEFAULT_SECTOR = "미분류"
TESLA_SECTOR = "테슬라"
DEFAULT_INCLUDE_FLAG = "Y"
DEFAULT_FIXED_FLAG = "N"
FIXED_FLAG = "Y"
INCLUDED_FLAG = "Y"


@dataclass(slots=True)
class SectorHolding:
    matching_key: str
    symbol: str
    name: str
    amount: Decimal


@dataclass(slots=True)
class SectorClassification:
    matching_key: str
    symbol: str
    name: str
    sector: str
    include_flag: str
    fixed_flag: str
    memo: str

    def to_sheet_row(self) -> list[str]:
        return [
            self.matching_key,
            self.symbol,
            self.name,
            self.sector,
            self.include_flag,
            self.fixed_flag,
            self.memo,
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

        for title in (
            LATEST_ASSET_SHEET,
            ADDITIONAL_ASSET_SHEET,
            RUN_LOG_SHEET,
            DAILY_TREND_SHEET,
            SECTOR_CLASSIFICATION_SHEET,
            SECTOR_STATUS_SHEET,
        ):
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
        self._sync_header(SECTOR_CLASSIFICATION_SHEET, SECTOR_CLASSIFICATION_HEADERS)
        self._sync_header(SECTOR_STATUS_SHEET, SECTOR_STATUS_HEADERS)

    def refresh_latest_views(self, records: list[AssetRecord]) -> None:
        latest_rows, _ = summarize_latest(records)
        latest_payload = [LATEST_HEADERS] + latest_rows
        self._replace_sheet(LATEST_ASSET_SHEET, latest_payload)

    def refresh_sector_views(
        self,
        records: list[AssetRecord],
        *,
        captured_at: str,
        timezone: str,
    ) -> None:
        local_captured_at = _to_local_datetime(captured_at, timezone).isoformat(timespec="seconds")
        additional_rows = self.service.spreadsheets().values().get(
            spreadsheetId=self.spreadsheet_id,
            range=f"{ADDITIONAL_ASSET_SHEET}!A2:L",
        ).execute()
        holdings = _build_sector_holdings(
            records,
            additional_rows=additional_rows.get("values", []),
        )

        existing = self.service.spreadsheets().values().get(
            spreadsheetId=self.spreadsheet_id,
            range=f"{SECTOR_CLASSIFICATION_SHEET}!A2:G",
        ).execute()
        classifications = _sync_sector_classifications(
            holdings,
            existing.get("values", []),
        )
        self._replace_sheet(
            SECTOR_CLASSIFICATION_SHEET,
            [SECTOR_CLASSIFICATION_HEADERS] + [item.to_sheet_row() for item in classifications],
        )
        self._replace_sheet(
            SECTOR_STATUS_SHEET,
            [SECTOR_STATUS_HEADERS] + _build_sector_status_rows(holdings, classifications, local_captured_at),
        )

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


def _build_sector_holdings(records: list[AssetRecord], *, additional_rows: list[list[str]]) -> list[SectorHolding]:
    holdings: dict[str, SectorHolding] = {}
    for record in _latest_records(records):
        amount = record.amount_in_krw or Decimal("0")
        if amount == 0:
            continue
        holding = _sector_holding_from_values(record.symbol, record.name, amount)
        if holding is None:
            continue
        holdings[holding.matching_key] = _merge_sector_holding(holdings.get(holding.matching_key), holding)

    for row in additional_rows:
        amount = parse_decimal(_get_row_value(row, 11)) or Decimal("0")
        if amount == 0:
            continue
        holding = _sector_holding_from_values(_get_row_value(row, 6), _get_row_value(row, 7), amount)
        if holding is None:
            continue
        holdings[holding.matching_key] = _merge_sector_holding(holdings.get(holding.matching_key), holding)

    return sorted(holdings.values(), key=lambda item: item.matching_key)


def _sector_holding_from_values(symbol: str, name: str, amount: Decimal) -> SectorHolding | None:
    matching_key = _sector_matching_key(symbol, name)
    if not matching_key:
        return None
    return SectorHolding(
        matching_key=matching_key,
        symbol=symbol.strip(),
        name=name.strip(),
        amount=amount,
    )


def _merge_sector_holding(existing: SectorHolding | None, incoming: SectorHolding) -> SectorHolding:
    if existing is None:
        return incoming
    return SectorHolding(
        matching_key=existing.matching_key,
        symbol=existing.symbol or incoming.symbol,
        name=existing.name or incoming.name,
        amount=existing.amount + incoming.amount,
    )


def _sync_sector_classifications(
    holdings: list[SectorHolding],
    rows: list[list[str]],
) -> list[SectorClassification]:
    current_keys = {holding.matching_key for holding in holdings}
    holdings_by_key = {holding.matching_key: holding for holding in holdings}
    existing_by_key: dict[str, SectorClassification] = {}
    fixed_rows: list[SectorClassification] = []

    for row in rows:
        classification = _parse_sector_classification_row(row)
        if classification is None:
            continue
        if classification.matching_key in current_keys:
            holding = holdings_by_key[classification.matching_key]
            existing_by_key[classification.matching_key] = SectorClassification(
                matching_key=classification.matching_key,
                symbol=holding.symbol or classification.symbol,
                name=holding.name or classification.name,
                sector=classification.sector,
                include_flag=classification.include_flag,
                fixed_flag=classification.fixed_flag,
                memo=classification.memo,
            )
        elif classification.fixed_flag.upper() == FIXED_FLAG:
            fixed_rows.append(classification)

    synced: list[SectorClassification] = []
    for holding in holdings:
        synced.append(
            existing_by_key.get(
                holding.matching_key,
                SectorClassification(
                    matching_key=holding.matching_key,
                    symbol=holding.symbol,
                    name=holding.name,
                    sector=DEFAULT_SECTOR,
                    include_flag=DEFAULT_INCLUDE_FLAG,
                    fixed_flag=DEFAULT_FIXED_FLAG,
                    memo="",
                ),
            )
        )
    synced.extend(fixed_rows)
    return synced


def _parse_sector_classification_row(row: list[str]) -> SectorClassification | None:
    matching_key = _get_row_value(row, 0)
    symbol = _get_row_value(row, 1)
    name = _get_row_value(row, 2)
    if not matching_key:
        matching_key = _sector_matching_key(symbol, name)
    if not matching_key:
        return None
    return SectorClassification(
        matching_key=matching_key,
        symbol=symbol,
        name=name,
        sector=_get_row_value(row, 3) or DEFAULT_SECTOR,
        include_flag=(_get_row_value(row, 4) or DEFAULT_INCLUDE_FLAG).upper(),
        fixed_flag=(_get_row_value(row, 5) or DEFAULT_FIXED_FLAG).upper(),
        memo=_get_row_value(row, 6),
    )


def _build_sector_status_rows(
    holdings: list[SectorHolding],
    classifications: list[SectorClassification],
    captured_at: str,
) -> list[list[str]]:
    classifications_by_key = {item.matching_key: item for item in classifications}
    totals_by_sector: dict[str, Decimal] = {}
    holdings_by_sector: dict[str, list[SectorHolding]] = {}

    for holding in holdings:
        classification = classifications_by_key.get(holding.matching_key)
        if classification is None or classification.include_flag.upper() != INCLUDED_FLAG:
            continue
        sector = classification.sector or DEFAULT_SECTOR
        totals_by_sector[sector] = totals_by_sector.get(sector, Decimal("0")) + holding.amount
        holdings_by_sector.setdefault(sector, []).append(holding)

    total = sum(totals_by_sector.values(), Decimal("0"))
    total_excluding_tesla = total - totals_by_sector.get(TESLA_SECTOR, Decimal("0"))

    rows: list[list[str]] = []
    for sector in _ordered_sectors(totals_by_sector):
        amount = totals_by_sector[sector]
        sector_holdings = sorted(holdings_by_sector.get(sector, []), key=lambda item: item.amount, reverse=True)
        excluding_tesla_ratio = Decimal("0")
        if sector != TESLA_SECTOR and total_excluding_tesla:
            excluding_tesla_ratio = amount / total_excluding_tesla
        rows.append(
            [
                captured_at,
                sector,
                _decimal_to_string(amount),
                _percent_to_string(amount / total if total else Decimal("0")),
                _percent_to_string(excluding_tesla_ratio),
                str(len(sector_holdings)),
                ", ".join(holding.name or holding.symbol or holding.matching_key for holding in sector_holdings[:5]),
            ]
        )
    return rows


def _ordered_sectors(totals_by_sector: dict[str, Decimal]) -> list[str]:
    preferred = [
        TESLA_SECTOR,
        "반도체",
        "전력",
        "휴머노이드",
        "우주",
        "코어",
        "현금",
        "코인",
        "기타",
        DEFAULT_SECTOR,
    ]
    ordered = [sector for sector in preferred if sector in totals_by_sector]
    ordered.extend(sorted(sector for sector in totals_by_sector if sector not in set(preferred)))
    return ordered


def _sector_matching_key(symbol: str, name: str) -> str:
    normalized_symbol = symbol.strip().upper()
    if normalized_symbol:
        return normalized_symbol
    return " ".join(name.strip().split()).upper()


def _percent_to_string(value: Decimal) -> str:
    return f"{value * Decimal('100'):.2f}%"


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

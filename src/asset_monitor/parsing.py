from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Iterable

from bs4 import BeautifulSoup

from .models import AssetRecord

NUMBER_RE = re.compile(r"-?\d[\d,]*\.?\d*")
CURRENCY_RE = re.compile(r"\b(KRW|USD|JPY|HKD|EUR|CNY|CNH|GBP)\b", re.IGNORECASE)


def parse_decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    cleaned = value.strip().replace("\u2212", "-")
    if not cleaned:
        return None
    match = NUMBER_RE.search(cleaned)
    if not match:
        return None
    numeric = match.group(0).replace(",", "")
    try:
        return Decimal(numeric)
    except InvalidOperation:
        return None


def parse_currency(value: str | None, default: str = "KRW") -> str:
    if not value:
        return default
    match = CURRENCY_RE.search(value)
    if match:
        return match.group(1).upper()
    text = value.strip()
    if "원" in text:
        return "KRW"
    return default


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.replace("\xa0", " ").split())


def first_non_empty(values: Iterable[str | None], default: str = "") -> str:
    for value in values:
        text = clean_text(value)
        if text:
            return text
    return default


def infer_cash_subtype(name: str, currency: str) -> str:
    lowered = name.lower()
    if "rp" in lowered:
        return "rp"
    if currency and currency.upper() != "KRW":
        return "fx_cash"
    if "외화" in name:
        return "fx_cash"
    return "krw_cash"


def parse_table_html(
    html: str,
    *,
    captured_at: str,
    broker_name: str,
    owner_name: str,
    account_name: str,
    account_masked_id: str,
    asset_group: str,
    source_page: str,
    column_map: dict[str, int],
    default_market: str = "",
    default_currency: str = "KRW",
) -> list[AssetRecord]:
    soup = BeautifulSoup(html, "html.parser")
    records: list[AssetRecord] = []

    for row in soup.select("tr"):
        cells = [clean_text(cell.get_text(" ", strip=True)) for cell in row.select("th,td")]
        if not cells:
            continue
        if _looks_like_header(cells):
            continue

        name = _get_cell(cells, column_map, "name")
        if not name:
            continue
        if "조회된 데이터가 없습니다" in name:
            continue

        symbol = _get_cell(cells, column_map, "symbol")
        quantity = parse_decimal(_get_cell(cells, column_map, "quantity"))
        amount_unit = parse_decimal(_get_cell(cells, column_map, "amount_in_unit_currency"))
        amount_krw = parse_decimal(_get_cell(cells, column_map, "amount_in_krw"))
        fx_rate = parse_decimal(_get_cell(cells, column_map, "fx_rate_to_krw"))
        market = _get_cell(cells, column_map, "market") or default_market
        unit_currency = parse_currency(_get_cell(cells, column_map, "unit_currency"), default_currency)

        if asset_group == "cash_equivalent":
            asset_subtype = infer_cash_subtype(name, unit_currency)
        else:
            asset_subtype = "stock"

        if amount_krw is None and amount_unit is not None and fx_rate is not None:
            amount_krw = amount_unit * fx_rate

        records.append(
            AssetRecord(
                captured_at=captured_at,
                broker_name=broker_name,
                owner_name=owner_name,
                account_name=account_name,
                account_masked_id=account_masked_id,
                asset_group=asset_group,
                asset_subtype=asset_subtype,
                market=market,
                symbol=symbol,
                name=name,
                quantity=quantity,
                unit_currency=unit_currency,
                amount_in_unit_currency=amount_unit,
                fx_rate_to_krw=fx_rate,
                amount_in_krw=amount_krw,
                source_page=source_page,
            )
        )

    return records


def parse_foreign_response_payload(
    payload: dict,
    *,
    captured_at: str,
    broker_name: str,
    owner_name: str,
    account_name: str,
    account_masked_id: str,
    source_page: str = "foreign",
) -> list[AssetRecord]:
    body = payload.get("body") or {}
    positions = body.get("list") or []
    records: list[AssetRecord] = []

    for position in positions:
        if not isinstance(position, dict):
            continue

        currency = clean_text(_pick(position, "통화코드", "currencyCode", "currency")) or "USD"
        quantity = parse_decimal(_pick(position, "해외증권잔고수량", "결제수량", "quantity"))
        amount_unit = parse_decimal(_pick(position, "평가금액", "amountInUnitCurrency"))
        fx_rate = parse_decimal(_pick(position, "환산환율", "환율", "fxRateToKrw"))
        amount_krw = amount_unit * fx_rate if amount_unit is not None and fx_rate is not None else None

        name = first_non_empty(
            [
                _pick(position, "종목명", "name"),
                _pick(position, "종목영문명", "englishName"),
                _pick(position, "ISIN코드", "symbol"),
                _pick(position, "종목코드", "securityCode"),
            ]
        )
        symbol = first_non_empty(
            [
                _pick(position, "ISIN코드", "symbol"),
                _pick(position, "종목코드", "securityCode"),
            ]
        )
        market = first_non_empty(
            [
                _pick(position, "국가명", "countryName"),
                _pick(position, "해외시장구분명", "marketName"),
            ]
        )

        if not name:
            continue

        records.append(
            AssetRecord(
                captured_at=captured_at,
                broker_name=broker_name,
                owner_name=owner_name,
                account_name=account_name,
                account_masked_id=account_masked_id,
                asset_group="foreign_stock",
                asset_subtype="stock",
                market=market,
                symbol=symbol,
                name=name,
                quantity=quantity,
                unit_currency=currency,
                amount_in_unit_currency=amount_unit,
                fx_rate_to_krw=fx_rate,
                amount_in_krw=amount_krw,
                source_page=source_page,
            )
        )

    return records


def parse_cash_response_payload(
    payload: dict,
    *,
    captured_at: str,
    broker_name: str,
    owner_name: str,
    account_name: str,
    account_masked_id: str,
    source_page: str = "cash",
) -> list[AssetRecord]:
    body = payload.get("body") or {}
    records: list[AssetRecord] = []

    for name, candidate_keys, subtype in (
        ("CMA", ("CMA평가금액", "cmaAmount"), "krw_cash"),
        ("외화RP", ("외화RP평가금액", "fxRpAmount"), "rp"),
    ):
        amount_krw = None
        for key in candidate_keys:
            amount_krw = parse_decimal(body.get(key))
            if amount_krw is not None:
                break
        if amount_krw is None:
            continue

        records.append(
            AssetRecord(
                captured_at=captured_at,
                broker_name=broker_name,
                owner_name=owner_name,
                account_name=account_name,
                account_masked_id=account_masked_id,
                asset_group="cash_equivalent",
                asset_subtype=subtype,
                market="",
                symbol="",
                name=name,
                quantity=None,
                unit_currency="KRW",
                amount_in_unit_currency=amount_krw,
                fx_rate_to_krw=None,
                amount_in_krw=amount_krw,
                source_page=source_page,
            )
        )

    return records


def summarize_latest(records: list[AssetRecord]) -> tuple[list[list[str]], list[list[str]]]:
    latest: dict[tuple[str, str, str, str, str, str], AssetRecord] = {}
    for record in sorted(records, key=lambda item: item.captured_at):
        latest[record.identity_key()] = record

    latest_rows = [record.to_sheet_row() for record in latest.values()]

    by_owner_group: dict[tuple[str, str], Decimal] = {}
    by_owner_total: dict[str, Decimal] = {}
    grand_total = Decimal("0")

    for record in latest.values():
        amount_krw = record.amount_in_krw or Decimal("0")
        owner_group_key = (record.owner_name, record.asset_group)
        by_owner_group[owner_group_key] = by_owner_group.get(owner_group_key, Decimal("0")) + amount_krw
        by_owner_total[record.owner_name] = by_owner_total.get(record.owner_name, Decimal("0")) + amount_krw
        grand_total += amount_krw

    summary_rows = [["구분", "값"]]
    for (owner_name, asset_group), amount in sorted(by_owner_group.items()):
        summary_rows.append([f"{owner_name} {_asset_group_summary_label(asset_group)}", format(amount, "f")])
    for owner_name, amount in sorted(by_owner_total.items()):
        summary_rows.append([f"{owner_name} 총자산(원화환산)", format(amount, "f")])
    summary_rows.append(["전체 총자산(원화환산)", format(grand_total, "f")])
    summary_rows.append(["기준 시각", max((record.captured_at for record in latest.values()), default="")])
    return latest_rows, summary_rows


def _pick(payload: dict, *keys: str) -> str:
    return first_non_empty(payload.get(key) for key in keys)


def _get_cell(cells: list[str], column_map: dict[str, int], key: str) -> str:
    index = column_map.get(key)
    if index is None or index >= len(cells):
        return ""
    return cells[index]


def _looks_like_header(cells: list[str]) -> bool:
    joined = " ".join(cells).lower()
    header_keywords = ("종목", "평가", "수량", "통화", "잔고", "금액", "symbol", "name", "qty", "market")
    return any(keyword in joined for keyword in header_keywords) and not NUMBER_RE.search(joined)


def _asset_group_summary_label(asset_group: str) -> str:
    labels = {
        "domestic_stock": "국내주식 합계(원화환산)",
        "foreign_stock": "해외주식 합계(원화환산)",
        "cash_equivalent": "현금성자산 합계(원화환산)",
        "broker_position": "보유상품 합계(원화환산)",
    }
    return labels.get(asset_group, asset_group)

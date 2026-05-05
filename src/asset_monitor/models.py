from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any


ASSET_GROUP_LABELS = {
    "domestic_stock": "국내주식",
    "foreign_stock": "해외주식",
    "cash_equivalent": "현금성자산",
    "broker_position": "보유상품",
}

BROKER_LABELS = {
    "shinhan": "신한투자증권",
    "miraeasset": "미래에셋증권",
}

ASSET_SUBTYPE_LABELS = {
    "stock": "주식",
    "krw_cash": "원화예수금",
    "fx_cash": "외화예수금",
    "rp": "RP",
    "personal_pension": "개인연금",
    "retirement_pension": "퇴직연금",
}

SOURCE_PAGE_LABELS = {
    "domestic": "국내주식",
    "foreign": "해외주식",
    "cash": "금융상품",
    "miraeasset_account_holdings": "상품보유현황",
    "miraeasset_personal_pension_holdings": "개인연금 보유상품현황",
    "miraeasset_retirement_pension_holdings": "퇴직연금 보유상품현황",
}

RUN_STATUS_LABELS = {
    "success": "성공",
    "failed": "실패",
}


def _decimal_to_string(value: Decimal | None) -> str:
    if value is None:
        return ""
    return format(value, "f")


def _label(mapping: dict[str, str], value: str) -> str:
    if mapping is BROKER_LABELS and value == "samsung":
        return "삼성증권"
    if mapping is BROKER_LABELS and value == "kiwoom":
        return "키움증권"
    if mapping is BROKER_LABELS and value == "upbit":
        return "Upbit"
    if mapping is ASSET_GROUP_LABELS and value == "crypto_asset":
        return "코인"
    return mapping.get(value, value)


@dataclass(slots=True)
class AssetRecord:
    captured_at: str
    broker_name: str
    owner_name: str
    account_name: str
    account_masked_id: str
    asset_group: str
    asset_subtype: str
    market: str
    symbol: str
    name: str
    quantity: Decimal | None
    unit_currency: str
    amount_in_unit_currency: Decimal | None
    fx_rate_to_krw: Decimal | None
    amount_in_krw: Decimal | None
    source_page: str

    def identity_key(self) -> tuple[str, str, str, str, str, str]:
        symbol_or_name = self.symbol or self.name
        return (
            self.broker_name,
            self.owner_name,
            self.account_name or self.account_masked_id,
            self.asset_group,
            symbol_or_name,
            self.asset_subtype,
        )

    def to_sheet_row(self) -> list[str]:
        return [
            self.captured_at,
            _label(BROKER_LABELS, self.broker_name),
            self.owner_name,
            _label(ASSET_GROUP_LABELS, self.asset_group),
            _label(ASSET_SUBTYPE_LABELS, self.asset_subtype),
            self.market,
            self.symbol,
            self.name,
            _decimal_to_string(self.quantity),
            self.unit_currency,
            _decimal_to_string(self.fx_rate_to_krw),
            _decimal_to_string(self.amount_in_krw),
        ]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for field in ("quantity", "amount_in_unit_currency", "fx_rate_to_krw", "amount_in_krw"):
            payload[field] = _decimal_to_string(payload[field])
        return payload


@dataclass(slots=True)
class RunLogEntry:
    captured_at: str
    broker_name: str
    owner_name: str
    status: str
    total_records: int
    domestic_records: int
    foreign_records: int
    cash_records: int
    message: str
    debug_dir: str

    def to_sheet_row(self) -> list[str]:
        return [
            self.captured_at,
            _label(BROKER_LABELS, self.broker_name),
            self.owner_name,
            _label(RUN_STATUS_LABELS, self.status),
            str(self.total_records),
            str(self.domestic_records),
            str(self.foreign_records),
            str(self.cash_records),
            self.message,
            self.debug_dir,
        ]

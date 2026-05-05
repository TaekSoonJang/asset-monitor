from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from playwright.sync_api import Frame, Page, sync_playwright

from asset_monitor.config import AccountConfig
from asset_monitor.debug import save_page_debug
from asset_monitor.models import AssetRecord
from asset_monitor.parsing import clean_text, parse_decimal

from .config import SamsungBrokerConfig


class SamsungCollector:
    def __init__(
        self,
        broker_config: SamsungBrokerConfig,
        account: AccountConfig,
        debug_dir: Path,
    ) -> None:
        self.broker_config = broker_config
        self.account = account
        self.debug_dir = debug_dir

    def collect(self, captured_at: str) -> dict[str, list[AssetRecord]]:
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(self.account.cdp_url)
            if not browser.contexts:
                raise RuntimeError("Samsung Securities requires an already-open logged-in browser context.")
            page = self._find_existing_page(browser.contexts[0])
            page.set_default_timeout(15000)
            page.set_default_navigation_timeout(30000)

            frame = self._open_my_asset_page(page)
            payload = self._fetch_type_status(frame)
            save_page_debug(page, self.debug_dir, "samsung_my_asset")
            records = build_samsung_records(
                payload,
                captured_at=captured_at,
                owner_name=self.account.name,
            )
            if not records["domestic"] and not records["foreign"] and not records["cash"]:
                raise RuntimeError("Samsung Securities returned no MY asset rows.")
            return records

    def _find_existing_page(self, context) -> Page:
        for page in context.pages:
            if "samsungpop.com" in page.url:
                return page
        raise RuntimeError(
            "Samsung Securities does not share login sessions with new tabs. "
            "Open and log in to samsungpop.com in the configured Chrome profile before running."
        )

    def _open_my_asset_page(self, page: Page) -> Frame:
        frame = self._content_frame(page)
        frame.goto(self.broker_config.routes.main_url, wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle", timeout=15000)
        page.wait_for_timeout(2000)
        return frame

    def _content_frame(self, page: Page) -> Frame:
        for frame in page.frames:
            if frame.name == "content":
                return frame
        raise RuntimeError("Samsung Securities content frame was not found.")

    def _fetch_type_status(self, frame: Frame) -> dict[str, Any]:
        payload = frame.evaluate(
            """
            async (url) => {
              const response = await fetch(url, {
                credentials: 'include',
                headers: {
                  'Accept': 'application/json',
                  'X-Requested-With': 'XMLHttpRequest',
                },
              });
              if (!response.ok) {
                throw new Error(`getTypeStatus failed: ${response.status}`);
              }
              const text = await response.text();
              try {
                return JSON.parse(text);
              } catch (error) {
                throw new Error('getTypeStatus did not return JSON. Check Samsung Securities login session.');
              }
            }
            """,
            self.broker_config.routes.type_status_url,
        )
        if not isinstance(payload, dict):
            raise RuntimeError("Unexpected Samsung Securities getTypeStatus response.")
        info = payload.get("info") or {}
        if isinstance(info, dict) and info.get("login") is False:
            raise RuntimeError("Samsung Securities login session is not active.")
        return payload


def build_samsung_records(
    payload: dict[str, Any],
    *,
    captured_at: str,
    owner_name: str,
) -> dict[str, list[AssetRecord]]:
    domestic: list[AssetRecord] = []
    foreign: list[AssetRecord] = []
    cash: list[AssetRecord] = []

    result = payload.get("result") or []
    if not isinstance(result, list):
        return {"domestic": domestic, "foreign": foreign, "cash": cash}

    for item in result:
        if not isinstance(item, dict):
            continue

        name = clean_text(_field(item, "ISUS_NAME"))
        if not name:
            continue

        quantity = parse_decimal(_field(item, "DCPN_BLNC_QNTY"))
        amount_in_krw = parse_decimal(_field(item, "A_VLTN_AMNT21"))
        if amount_in_krw is None:
            amount_in_krw = parse_decimal(_field(item, "A_AMNT_CTNT1"))
        account_masked_id = clean_text(_field(item, "A_UMS_MASK_ACNT_NO"))
        symbol = clean_text(_field(item, "ISCD")) or clean_text(_field(item, "ISUS_SHCD"))

        if _is_cash_row(item):
            amount = amount_in_krw or Decimal("0")
            cash.append(
                AssetRecord(
                    captured_at=captured_at,
                    broker_name="samsung",
                    owner_name=owner_name,
                    account_name="",
                    account_masked_id=account_masked_id,
                    asset_group="cash_equivalent",
                    asset_subtype="krw_cash",
                    market="Samsung POP",
                    symbol="KRW",
                    name=name,
                    quantity=amount,
                    unit_currency="KRW",
                    amount_in_unit_currency=amount,
                    fx_rate_to_krw=None,
                    amount_in_krw=amount,
                    source_page="samsung_my_asset_type_status",
                )
            )
            continue

        record = AssetRecord(
            captured_at=captured_at,
            broker_name="samsung",
            owner_name=owner_name,
            account_name="",
            account_masked_id=account_masked_id,
            asset_group=_asset_group(item),
            asset_subtype="stock",
            market="",
            symbol=symbol,
            name=name,
            quantity=quantity,
            unit_currency="KRW",
            amount_in_unit_currency=amount_in_krw,
            fx_rate_to_krw=None,
            amount_in_krw=amount_in_krw,
            source_page="samsung_my_asset_type_status",
        )
        if record.asset_group == "domestic_stock":
            domestic.append(record)
        else:
            foreign.append(record)

    return {"domestic": domestic, "foreign": foreign, "cash": cash}


def _field(item: dict[str, Any], key: str) -> str | None:
    value = item.get(key)
    if value is None:
        return None
    return str(value)


def _is_cash_row(item: dict[str, Any]) -> bool:
    product_class = clean_text(_field(item, "STND_PRDT_CLSN_CODE"))
    return product_class.startswith("N")


def _asset_group(item: dict[str, Any]) -> str:
    currency_section = clean_text(_field(item, "KRW_FRGN_CRNY_SECT_CODE"))
    if currency_section == "2":
        return "foreign_stock"
    return "domestic_stock"

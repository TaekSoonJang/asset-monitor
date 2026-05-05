from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from asset_monitor.config import AccountConfig
from asset_monitor.debug import save_page_debug
from asset_monitor.models import AssetRecord
from asset_monitor.parsing import clean_text, parse_decimal

from .config import KiwoomBrokerConfig


class KiwoomCollector:
    def __init__(
        self,
        broker_config: KiwoomBrokerConfig,
        account: AccountConfig,
        debug_dir: Path,
    ) -> None:
        self.broker_config = broker_config
        self.account = account
        self.debug_dir = debug_dir

    def collect(self, captured_at: str) -> dict[str, list[AssetRecord]]:
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(self.account.cdp_url)
            context = browser.contexts[0] if browser.contexts else browser.new_context(locale="ko-KR")
            page = self._find_or_open_page(context)
            page.set_default_timeout(15000)
            page.set_default_navigation_timeout(30000)

            domestic = self._collect_domestic_positions(page, captured_at)
            foreign = self._collect_foreign_positions(page, captured_at)
            cash = self._collect_foreign_cash(page, captured_at)
            if not domestic and not foreign and not cash:
                raise RuntimeError("키움 화면에서 종목을 찾지 못했습니다.")
            return {"domestic": domestic, "foreign": foreign, "cash": cash}

    def _collect_domestic_positions(self, page: Page, captured_at: str) -> list[AssetRecord]:
        page.goto(self.broker_config.routes.domestic_url, wait_until="domcontentloaded")
        self._ensure_logged_in(page, self.broker_config.dom.domestic_search_button_id)
        self._select_account(page)
        self._set_password(page)
        self._run_search(page, "domestic")
        save_page_debug(page, self.debug_dir, "kiwoom_domestic")
        rows = page.evaluate(
            """
            () => Array.from(document.querySelectorAll('table.al-r.even-bg-row2 tr'))
              .map(row => Array.from(row.querySelectorAll('th,td')).map(cell => (cell.innerText || '').trim()))
              .filter(cells => cells.length > 0)
            """
        )
        return build_kiwoom_domestic_records(
            rows,
            captured_at=captured_at,
            owner_name=self.account.name,
        )

    def _collect_foreign_positions(self, page: Page, captured_at: str) -> list[AssetRecord]:
        page.goto(self.broker_config.routes.foreign_url, wait_until="domcontentloaded")
        self._ensure_logged_in(page, self.broker_config.dom.foreign_search_button_id)
        self._select_account(page)
        self._set_password(page)
        self._run_search(page, "foreign")
        save_page_debug(page, self.debug_dir, "kiwoom_foreign")
        rows = page.evaluate(
            """
            () => {
              const tables = Array.from(document.querySelectorAll('table.al-r'));
              const table = tables.find(candidate => {
                const header = Array.from(candidate.querySelectorAll('th')).map(cell => (cell.innerText || '').trim()).join(' ');
                return header.includes('종목명') && header.includes('보유량');
              });
              if (!table) {
                return [];
              }
              return Array.from(table.querySelectorAll('tr'))
                .map(row => Array.from(row.querySelectorAll('th,td')).map(cell => (cell.innerText || '').trim()))
                .filter(cells => cells.length > 0);
            }
            """
        )
        return build_kiwoom_foreign_records(
            rows,
            captured_at=captured_at,
            owner_name=self.account.name,
        )

    def _collect_foreign_cash(self, page: Page, captured_at: str) -> list[AssetRecord]:
        payload = page.evaluate(
            """
            () => {
              const krwCash = (document.querySelector('#won_entr')?.innerText || '').trim();
              const fxRows = Array.from(document.querySelectorAll('#TDI3013_Q03_g1 table tr'))
                .map(row => Array.from(row.querySelectorAll('th,td')).map(cell => (cell.innerText || '').trim()))
                .filter(cells => cells.length > 0);
              return { krwCash, fxRows };
            }
            """
        )
        return build_kiwoom_foreign_cash_records(
            payload.get("krwCash", ""),
            payload.get("fxRows", []),
            captured_at=captured_at,
            owner_name=self.account.name,
        )

    def _find_or_open_page(self, context) -> Page:
        for page in context.pages:
            if "kiwoom.com" in page.url:
                return page
        return context.new_page()

    def _ensure_logged_in(self, page: Page, search_button_id: str) -> None:
        if not page.locator(f"#{self.broker_config.dom.account_select_id}").count():
            raise RuntimeError("키움 계좌 선택기를 찾지 못했습니다.")
        if not page.locator(f"#{search_button_id}").count():
            raise RuntimeError("키움 조회 버튼을 찾지 못했습니다.")

    def _select_account(self, page: Page) -> None:
        account_number = self._require_setting("account_number")
        normalized = "".join(ch for ch in account_number if ch.isdigit())
        selected = page.evaluate(
            f"""
            (normalized) => {{
              const select = document.querySelector('#{self.broker_config.dom.account_select_id}');
              if (!select) {{
                return false;
              }}
              const options = Array.from(select.options);
              const index = options.findIndex(option => ((option.text || '').replace(/[^0-9]/g, '')).includes(normalized));
              if (index < 0) {{
                return false;
              }}
              select.selectedIndex = index;
              select.value = options[index].value;
              select.dispatchEvent(new Event('input', {{ bubbles: true }}));
              select.dispatchEvent(new Event('change', {{ bubbles: true }}));
              return true;
            }}
            """,
            normalized,
        )
        if not selected:
            raise RuntimeError(f"키움 계좌번호를 찾지 못했습니다: {account_number}")
        page.wait_for_timeout(300)

    def _set_password(self, page: Page) -> None:
        password = self._require_setting("account_inquiry_password")
        page.evaluate(
            f"""
            (password) => {{
              const input = document.querySelector('#{self.broker_config.dom.password_input_id}');
              if (!input) {{
                throw new Error('password input not found');
              }}
              input.removeAttribute('readonly');
              input.value = password;
              input.dispatchEvent(new Event('input', {{ bubbles: true }}));
              input.dispatchEvent(new Event('change', {{ bubbles: true }}));
            }}
            """,
            password,
        )

    def _run_search(self, page: Page, market: str) -> None:
        if market == "domestic":
            page.evaluate(
                """
                () => {
                  if (typeof fn_listAjax === 'function') {
                    fn_listAjax('search');
                    return;
                  }
                  const button = document.querySelector('#btn_search');
                  if (button) {
                    button.click();
                  }
                }
                """
            )
        else:
            page.evaluate(
                """
                () => {
                  if (typeof fn_clear === 'function') {
                    fn_clear();
                  }
                  if (typeof fn_list === 'function') {
                    fn_list();
                    return;
                  }
                  const button = document.querySelector('#btnSearch');
                  if (button) {
                    button.click();
                  }
                }
                """
            )
        self._wait_for_search_completion(page)

    def _wait_for_search_completion(self, page: Page) -> None:
        page.wait_for_load_state("networkidle", timeout=10000)
        page.wait_for_timeout(2000)

    def _setting(self, key: str) -> str | None:
        value = self.account.settings.get(key)
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _require_setting(self, key: str) -> str:
        value = self._setting(key)
        if not value:
            raise RuntimeError(f"Account '{self.account.name}' is missing broker setting '{key}'.")
        return value


def build_kiwoom_domestic_records(
    rows: list[list[str]],
    *,
    captured_at: str,
    owner_name: str,
) -> list[AssetRecord]:
    data_rows = [
        cells
        for cells in rows
        if len(cells) == 9 and not _looks_like_notice_row(cells) and not _looks_like_header_row(cells)
    ]
    records: list[AssetRecord] = []
    for index in range(0, len(data_rows), 2):
        first = data_rows[index]
        second = data_rows[index + 1] if index + 1 < len(data_rows) else []
        symbol = clean_text(first[0]) if first else ""
        name = clean_text(second[0]) if second else ""
        quantity = parse_decimal(first[4] if len(first) > 4 else "")
        amount_in_krw = parse_decimal(second[3] if len(second) > 3 else "")
        if not name:
            continue
        records.append(
            AssetRecord(
                captured_at=captured_at,
                broker_name="kiwoom",
                owner_name=owner_name,
                account_name="",
                account_masked_id="",
                asset_group="domestic_stock",
                asset_subtype="stock",
                market="KRX",
                symbol=symbol,
                name=name,
                quantity=quantity,
                unit_currency="KRW",
                amount_in_unit_currency=amount_in_krw,
                fx_rate_to_krw=None,
                amount_in_krw=amount_in_krw,
                source_page="kiwoom_domestic",
            )
        )
    return records


def build_kiwoom_foreign_records(
    rows: list[list[str]],
    *,
    captured_at: str,
    owner_name: str,
) -> list[AssetRecord]:
    data_rows = [
        cells
        for cells in rows
        if len(cells) >= 17 and not _looks_like_notice_row(cells) and not _looks_like_header_row(cells)
    ]
    records: list[AssetRecord] = []
    for cells in data_rows:
        name = clean_text(cells[0])
        quantity = parse_decimal(cells[1])
        amount_in_unit_currency = parse_decimal(cells[8])
        fx_rate_to_krw = parse_decimal(cells[10])
        amount_in_krw = _compute_amount_in_krw(amount_in_unit_currency, fx_rate_to_krw)
        unit_currency = clean_text(cells[15]) or "USD"
        symbol = clean_text(cells[16])
        if not name:
            continue
        records.append(
            AssetRecord(
                captured_at=captured_at,
                broker_name="kiwoom",
                owner_name=owner_name,
                account_name="",
                account_masked_id="",
                asset_group="foreign_stock",
                asset_subtype="stock",
                market="",
                symbol=symbol,
                name=name,
                quantity=quantity,
                unit_currency=unit_currency,
                amount_in_unit_currency=amount_in_unit_currency,
                fx_rate_to_krw=fx_rate_to_krw,
                amount_in_krw=amount_in_krw,
                source_page="kiwoom_foreign",
            )
        )
    return records


def build_kiwoom_foreign_cash_records(
    krw_cash_text: str,
    fx_rows: list[list[str]],
    *,
    captured_at: str,
    owner_name: str,
) -> list[AssetRecord]:
    records: list[AssetRecord] = []
    amount_in_krw = parse_decimal(krw_cash_text)
    if amount_in_krw is not None:
        records.append(
            AssetRecord(
                captured_at=captured_at,
                broker_name="kiwoom",
                owner_name=owner_name,
                account_name="",
                account_masked_id="",
                asset_group="cash_equivalent",
                asset_subtype="krw_cash",
                market="",
                symbol="KRW",
                name="원화예수금",
                quantity=amount_in_krw,
                unit_currency="KRW",
                amount_in_unit_currency=amount_in_krw,
                fx_rate_to_krw=None,
                amount_in_krw=amount_in_krw,
                source_page="kiwoom_foreign",
            )
        )

    data_rows = [
        cells
        for cells in fx_rows
        if len(cells) >= 5 and not _looks_like_notice_row(cells) and not _looks_like_header_row(cells)
    ]
    for cells in data_rows:
        unit_currency = clean_text(cells[0]).upper()
        amount_in_unit_currency = parse_decimal(cells[1])
        fx_rate_to_krw = parse_decimal(cells[3])
        amount_in_krw = parse_decimal(cells[4])
        if not unit_currency or amount_in_unit_currency is None:
            continue
        if amount_in_krw is None:
            amount_in_krw = _compute_amount_in_krw(amount_in_unit_currency, fx_rate_to_krw)
        records.append(
            AssetRecord(
                captured_at=captured_at,
                broker_name="kiwoom",
                owner_name=owner_name,
                account_name="",
                account_masked_id="",
                asset_group="cash_equivalent",
                asset_subtype="fx_cash",
                market="",
                symbol=unit_currency,
                name=f"{unit_currency} 외화예수금",
                quantity=amount_in_unit_currency,
                unit_currency=unit_currency,
                amount_in_unit_currency=amount_in_unit_currency,
                fx_rate_to_krw=fx_rate_to_krw,
                amount_in_krw=amount_in_krw,
                source_page="kiwoom_foreign",
            )
        )
    return records


def _looks_like_notice_row(cells: list[str]) -> bool:
    joined = clean_text(" ".join(cells))
    return (
        not joined
        or "조회 전에 결과를 확인" in joined
        or "조회가 완료" in joined
        or "처리결과" in joined
        or joined == "조회"
    )


def _looks_like_header_row(cells: list[str]) -> bool:
    header_tokens = {
        "종목코드",
        "종목명",
        "평가손익",
        "보유수량",
        "보유량",
        "매입가",
        "현재가",
        "평가금액",
    }
    return any(clean_text(cell) in header_tokens for cell in cells[:3])


def _compute_amount_in_krw(
    amount_in_unit_currency: Decimal | None,
    fx_rate_to_krw: Decimal | None,
) -> Decimal | None:
    if amount_in_unit_currency is None or fx_rate_to_krw is None:
        return None
    return amount_in_unit_currency * fx_rate_to_krw

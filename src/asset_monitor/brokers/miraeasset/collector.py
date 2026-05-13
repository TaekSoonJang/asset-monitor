from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import Frame, Page, sync_playwright

from asset_monitor.config import AccountConfig
from asset_monitor.debug import save_page_debug
from asset_monitor.models import AssetRecord
from asset_monitor.parsing import clean_text, parse_decimal

from .config import MiraeAssetBrokerConfig

RETIREMENT_HOLDINGS_MAX_ATTEMPTS = 3
RETIREMENT_HOLDINGS_RETRY_DELAY_MS = 5000
NO_DATA_MARKERS = (
    "보유 상품이 없습니다.",
    "조회 내역이 없습니다.",
    "조회할 펀드가 없습니다",
)


class MiraeAssetCollector:
    def __init__(
        self,
        broker_config: MiraeAssetBrokerConfig,
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

            holdings = self._collect_account_holdings(page, captured_at)
            personal_pension_holdings = self._collect_personal_pension_holdings(page, captured_at)
            retirement_pension_holdings = self._collect_retirement_pension_holdings(page, captured_at)
            records = holdings + personal_pension_holdings + retirement_pension_holdings
            self._canonicalize_retirement_symbols(records)
            if not records:
                raise RuntimeError("미래에셋 화면에서 종목을 찾지 못했습니다.")
            return {"domestic": records, "foreign": [], "cash": []}

    def _collect_account_holdings(self, page: Page, captured_at: str) -> list[AssetRecord]:
        if not self._setting("account_number"):
            return []
        frame = self._open_frame_path(page, self.broker_config.routes.account_assets_url)
        self._ensure_logged_in(frame)

        target_index = self._resolve_target_account_index(frame)
        self._select_account(frame, target_index)
        self._load_all_account_rows(frame)

        save_page_debug(page, self.debug_dir, "miraeasset_account_assets")
        return self._parse_account_holdings(frame, captured_at)

    def _collect_personal_pension_holdings(self, page: Page, captured_at: str) -> list[AssetRecord]:
        if not self._setting("pension_account_number"):
            return []
        frame = self._open_frame_path(page, self.broker_config.routes.personal_pension_balance_url)
        self._ensure_logged_in(frame)
        self._select_pension_account(frame)

        self._save_personal_pension_identity_probe(frame)
        save_page_debug(page, self.debug_dir, "miraeasset_personal_pension_balance")
        return self._parse_pension_holdings(frame, captured_at) + self._parse_pension_cash(frame, captured_at)

    def _collect_retirement_pension_holdings(self, page: Page, captured_at: str) -> list[AssetRecord]:
        if not self._setting("retirement_account_number"):
            return []

        for attempt in range(RETIREMENT_HOLDINGS_MAX_ATTEMPTS):
            frame = self._open_frame_path(page, self.broker_config.routes.retirement_pension_balance_url)
            self._ensure_logged_in(frame)
            frame = self._select_retirement_account(page, frame)
            self._load_all_retirement_rows(frame)

            records = self._parse_retirement_holdings(frame, captured_at)
            if records:
                save_page_debug(page, self.debug_dir, "miraeasset_retirement_pension_balance")
                return records

            if attempt < RETIREMENT_HOLDINGS_MAX_ATTEMPTS - 1:
                page.wait_for_timeout(RETIREMENT_HOLDINGS_RETRY_DELAY_MS)

        save_page_debug(page, self.debug_dir, "miraeasset_retirement_pension_balance_empty")
        raise RuntimeError(
            "미래에셋 퇴직연금 보유상품을 찾지 못했습니다. "
            f"{RETIREMENT_HOLDINGS_MAX_ATTEMPTS}회 재시도 후에도 화면이 비어 있습니다."
        )

    def _canonicalize_retirement_symbols(self, records: list[AssetRecord]) -> None:
        symbols_by_name: dict[str, str] = {}
        for record in records:
            if record.asset_subtype == "retirement_pension" or record.asset_group == "cash_equivalent":
                continue
            symbol = _normalize_symbol(record.symbol)
            name = clean_text(record.name)
            if name and symbol and not _is_retirement_product_symbol(symbol):
                symbols_by_name.setdefault(name, symbol)

        for record in records:
            if record.asset_subtype != "retirement_pension" or record.asset_group == "cash_equivalent":
                continue
            canonical_symbol = symbols_by_name.get(clean_text(record.name))
            if canonical_symbol and (not record.symbol or _is_retirement_product_symbol(record.symbol)):
                record.symbol = canonical_symbol

    def _find_or_open_page(self, context) -> Page:
        for page in context.pages:
            if "securities.miraeasset.com" in page.url:
                return page
        page = context.new_page()
        page.goto("https://securities.miraeasset.com/", wait_until="domcontentloaded")
        return page

    def _open_frame_path(self, page: Page, url: str) -> Frame:
        page.goto("https://securities.miraeasset.com/", wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        frame = self._content_frame(page)
        frame.goto(url, wait_until="domcontentloaded")
        frame.wait_for_timeout(3000)
        return frame

    def _content_frame(self, page: Page) -> Frame:
        for frame in page.frames:
            if frame.name == self.broker_config.dom.content_frame_name:
                return frame
        raise RuntimeError("미래에셋 contentframe을 찾지 못했습니다.")

    def _ensure_logged_in(self, frame: Frame) -> None:
        text = frame.evaluate("() => document.body ? document.body.innerText : ''")
        if "로그아웃" not in text and "MY자산" not in text and "MY개인연금" not in text and "퇴직연금잔고" not in text:
            raise RuntimeError("미래에셋 로그인 세션을 찾지 못했습니다.")

    def _resolve_target_account_index(self, frame: Frame) -> int:
        tbody_selector = f"#{self.broker_config.dom.account_list_tbody_id}"
        account_number = self._setting("account_number")
        rows = frame.evaluate(
            f"""
            () => {{
              const rows = Array.from(document.querySelectorAll('{tbody_selector} tr'));
              return rows.map((row, index) => {{
                const cells = Array.from(row.querySelectorAll('td')).map(cell => (cell.innerText || '').trim());
                return {{
                  index,
                  account_number: (cells[0] || '').replace(/[^0-9]/g, ''),
                  account_type: cells[1] || '',
                }};
              }});
            }}
            """
        )
        if not rows:
            raise RuntimeError("미래에셋 계좌 목록을 읽지 못했습니다.")

        if account_number:
            normalized = "".join(ch for ch in account_number if ch.isdigit())
            for row in rows:
                if row["account_number"] == normalized:
                    return int(row["index"])
            raise RuntimeError(f"설정한 미래에셋 계좌번호를 찾지 못했습니다: {account_number}")

        current_account = frame.evaluate("() => (window.common && common.accountNo) ? String(common.accountNo) : ''")
        current_account = "".join(ch for ch in current_account if ch.isdigit())
        if current_account:
            for row in rows:
                if row["account_number"] == current_account:
                    return int(row["index"])

        for row in rows:
            account_type = clean_text(row["account_type"])
            if "퇴직연금" not in account_type:
                return int(row["index"])

        return int(rows[0]["index"])

    def _select_account(self, frame: Frame, index: int) -> None:
        frame.evaluate("(idx) => getAccountInfo(idx, true)", index)
        frame.wait_for_timeout(2500)
        frame.wait_for_selector(f"#{self.broker_config.dom.account_holdings_tbody_id} tr")

    def _load_all_account_rows(self, frame: Frame) -> None:
        tbody_selector = f"#{self.broker_config.dom.account_holdings_tbody_id} tr"
        more_selector = f"#{self.broker_config.dom.account_holdings_more_id}"
        for _ in range(10):
            visible = frame.evaluate(
                f"""
                () => {{
                  const more = document.querySelector('{more_selector}');
                  if (!more) return false;
                  return window.getComputedStyle(more).display !== 'none';
                }}
                """
            )
            if not visible:
                return
            before = frame.locator(tbody_selector).count()
            frame.evaluate("() => getAccountInfoDetail(false)")
            frame.wait_for_timeout(1500)
            after = frame.locator(tbody_selector).count()
            if after <= before:
                return

    def _parse_account_holdings(self, frame: Frame, captured_at: str) -> list[AssetRecord]:
        payloads = self._fetch_account_holdings_payloads(frame)
        records = self._parse_account_holdings_payloads(payloads, captured_at)
        if records:
            return records

        tbody_selector = f"#{self.broker_config.dom.account_holdings_tbody_id}"
        rows: list[dict[str, str]] = frame.evaluate(
            f"""
            () => {{
              const rows = Array.from(document.querySelectorAll('{tbody_selector} tr'));
              return rows.map(row => {{
                const rawCells = Array.from(row.querySelectorAll('td'));
                const cells = rawCells.map(cell => (cell.innerText || '').trim());
                const link = rawCells[0]?.querySelector('a[href*="goItemDetailInfo"]');
                return {{
                  name: cells[0] || '',
                  symbol: extractItemSymbol(link?.getAttribute('href') || ''),
                  quantity: cells[1] || '',
                  evaluation_amount: cells[5] || '',
                }};
              }});

              function extractItemSymbol(value) {{
                const match = String(value || '').match(/goItemDetailInfo\\(["']([^"']+)/);
                return match ? match[1] : '';
              }}
            }}
            """
        )
        return self._build_records(
            captured_at=captured_at,
            rows=rows,
            source_page="miraeasset_account_holdings",
            quantity_key="quantity",
            evaluation_key="evaluation_amount",
            asset_subtype="stock",
        )

    def _fetch_account_holdings_payloads(self, frame: Frame) -> list[dict]:
        try:
            payloads = frame.evaluate(
                f"""
                async (url) => {{
                  if (typeof callAjaxObj !== 'function' || !window.common || !common.accountNo) {{
                    return [];
                  }}

                  const pages = [];
                  let next = {{
                    next_pd_lcls_cd: '',
                    next_crd_tcd: '',
                    next_itm_no: '',
                    next_buy_dt: '',
                    next_buy_srno: '',
                  }};

                  for (let index = 0; index < 20; index += 1) {{
                    const params = {{
                      acno: common.accountNo,
                      header_account: common.accountNo,
                      grid_cnt01: '10',
                      dat_tp1: '1',
                      next_pd_lcls_cd: next.next_pd_lcls_cd,
                      next_crd_tcd: next.next_crd_tcd,
                      next_itm_no: next.next_itm_no,
                      next_buy_dt: next.next_buy_dt,
                      next_buy_srno: next.next_buy_srno,
                    }};
                    const data = await new Promise((resolve) => {{
                      try {{
                        callAjaxObj({{
                          url,
                          data: params,
                          success: (payload) => resolve(payload || {{}}),
                        }});
                      }} catch (error) {{
                        resolve({{ __error__: String(error) }});
                      }}
                    }});

                    if (!data || data.__error__ || data.result === 'error') {{
                      break;
                    }}
                    pages.push(data);

                    if (data.continueYn === '0' || !Array.isArray(data.cts01) || data.cts01.length === 0) {{
                      break;
                    }}
                    const cursor = data.cts01[0] || {{}};
                    next = {{
                      next_pd_lcls_cd: cursor.next_pd_lcls_cd || '',
                      next_crd_tcd: cursor.next_crd_tcd || '',
                      next_itm_no: cursor.next_itm_no || '',
                      next_buy_dt: cursor.next_buy_dt || '',
                      next_buy_srno: cursor.next_buy_srno || '',
                    }};
                    if (!next.next_itm_no && !next.next_pd_lcls_cd && !next.next_crd_tcd) {{
                      break;
                    }}
                  }}
                  return pages;
                }}
                """,
                self.broker_config.ajax.account_holdings,
            )
        except Exception:
            return []
        if not isinstance(payloads, list):
            return []
        return [payload for payload in payloads if isinstance(payload, dict)]

    def _parse_account_holdings_payloads(self, payloads: list[dict], captured_at: str) -> list[AssetRecord]:
        rows: list[dict[str, str]] = []
        for payload in payloads:
            grid = payload.get("grid01") or []
            if not isinstance(grid, list):
                continue
            for item in grid:
                if not isinstance(item, dict):
                    continue
                rows.append(
                    {
                        "name": _field(item, "itm_nm"),
                        "symbol": _field(item, "itm_no"),
                        "quantity": _field(item, "hldg_q21_8"),
                        "evaluation_amount": _field(item, "ea26_8"),
                    }
                )

        return self._build_records(
            captured_at=captured_at,
            rows=rows,
            source_page="miraeasset_account_holdings",
            quantity_key="quantity",
            evaluation_key="evaluation_amount",
            asset_subtype="stock",
        )

    def _select_pension_account(self, frame: Frame) -> None:
        pension_account_number = self._setting("pension_account_number")
        if not pension_account_number:
            return

        options = frame.evaluate(
            """
            () => {
              const select = document.querySelector('#userAccount');
              if (!select) return [];
              return Array.from(select.options).map((option, index) => ({
                index,
                value: option.value || '',
                text: option.text || '',
              }));
            }
            """
        )
        normalized = "".join(ch for ch in pension_account_number if ch.isdigit())
        for option in options:
            candidate = "".join(ch for ch in option["text"] if ch.isdigit())
            if candidate == normalized:
                frame.evaluate(
                    """
                    ({ index }) => {
                      const select = document.querySelector('#userAccount');
                      if (!select) {
                        throw new Error('userAccount select not found');
                      }
                      select.selectedIndex = index;
                      select.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                    """,
                    {"index": int(option["index"])},
                )
                frame.wait_for_timeout(2000)
                return

    def _parse_pension_holdings(self, frame: Frame, captured_at: str) -> list[AssetRecord]:
        payload = self._fetch_pension_holdings_payload(frame)
        records = self._parse_pension_holdings_payload(payload, captured_at)
        if records:
            return records

        tbody_selector = f"#{self.broker_config.dom.pension_holdings_tbody_id}"
        rows: list[dict[str, str]] = frame.evaluate(
            f"""
            () => {{
              const rows = Array.from(document.querySelectorAll('{tbody_selector} tr'));
              const items = [];
              for (let i = 0; i < rows.length; i += 2) {{
                const firstRaw = Array.from(rows[i]?.querySelectorAll('td') || []);
                const first = firstRaw.map(cell => (cell.innerText || '').trim());
                const second = Array.from(rows[i + 1]?.querySelectorAll('td') || []).map(cell => (cell.innerText || '').trim());
                const link = firstRaw[0]?.querySelector('a[href*="goItemDetailInfo"], a[href*="item_cd"]');
                items.push({{
                  name: first[0] || '',
                  symbol: extractItemSymbol(link?.getAttribute('href') || ''),
                  currency: first[1] || '',
                  quantity: first[2] || '',
                  evaluation_amount: first[4] || '',
                  current_price: second[0] || '',
                  buy_amount: second[1] || '',
                  profit_amount: second[2] || '',
                }});
              }}
              return items;

              function extractItemSymbol(value) {{
                const text = String(value || '');
                const detailMatch = text.match(/goItemDetailInfo\\(["']([^"']+)/);
                if (detailMatch) return detailMatch[1];
                const itemMatch = text.match(/[?&]item_cd=([^&"']+)/);
                return itemMatch ? decodeURIComponent(itemMatch[1]) : '';
              }}
            }}
            """
        )
        return self._build_records(
            captured_at=captured_at,
            rows=rows,
            source_page="miraeasset_personal_pension_holdings",
            quantity_key="quantity",
            evaluation_key="evaluation_amount",
            asset_subtype="personal_pension",
        )

    def _fetch_pension_holdings_payload(self, frame: Frame) -> dict:
        try:
            payload = frame.evaluate(
                f"""
                async (url) => {{
                  if (typeof callAjaxObj !== 'function' || !window.common || !common.accountNo) {{
                    return {{}};
                  }}
                  const params = {{
                    header_account: common.accountNo,
                    acno: common.accountNo,
                    dat_tp1: '3',
                  }};
                  return await new Promise((resolve) => {{
                    try {{
                      callAjaxObj({{
                        url,
                        data: params,
                        success: (data) => resolve(data || {{}}),
                      }});
                    }} catch (error) {{
                      resolve({{ __error__: String(error) }});
                    }}
                  }});
                }}
                """,
                self.broker_config.ajax.pension_holdings,
            )
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _parse_pension_holdings_payload(self, payload: dict, captured_at: str) -> list[AssetRecord]:
        rows: list[dict[str, str]] = []
        grid = payload.get("grid01") or []
        if not isinstance(grid, list):
            return []
        for item in grid:
            if not isinstance(item, dict):
                continue
            rows.append(
                {
                    "name": _field(item, "itm_nm"),
                    "symbol": _field(item, "itm_no"),
                    "quantity": _field(item, "hldg_q21_8"),
                    "evaluation_amount": _field(item, "ea26_8", "frc_ea"),
                }
            )

        return self._build_records(
            captured_at=captured_at,
            rows=rows,
            source_page="miraeasset_personal_pension_holdings",
            quantity_key="quantity",
            evaluation_key="evaluation_amount",
            asset_subtype="personal_pension",
        )

    def _parse_pension_cash(self, frame: Frame, captured_at: str) -> list[AssetRecord]:
        cash_text = frame.evaluate(
            """
            () => {
              const el = document.querySelector('#dpoa');
              return el ? (el.innerText || '').trim() : '';
            }
            """
        )
        amount_in_krw = parse_decimal(cash_text)
        if amount_in_krw is None:
            return []

        return [
            AssetRecord(
                captured_at=captured_at,
                broker_name="miraeasset",
                owner_name=self.account.name,
                account_name="",
                account_masked_id="",
                asset_group="cash_equivalent",
                asset_subtype="personal_pension",
                market="",
                symbol="",
                name="예수금",
                quantity=None,
                unit_currency="KRW",
                amount_in_unit_currency=amount_in_krw,
                fx_rate_to_krw=None,
                amount_in_krw=amount_in_krw,
                source_page="miraeasset_personal_pension_holdings",
            )
        ]

    def _save_personal_pension_identity_probe(self, frame: Frame) -> None:
        try:
            payload = frame.evaluate(
                """
                () => {
                  const globals = Object.keys(window)
                    .filter(key => /hkp1002|pension|account|acno|param|a24|grid|list|common/i.test(key))
                    .slice(0, 250);
                  const functions = {};
                  for (const key of globals) {
                    if (typeof window[key] === 'function') {
                      functions[key] = String(window[key]).slice(0, 5000);
                    }
                  }
                  const anchors = Array.from(document.querySelectorAll('a')).map(anchor => ({
                    text: (anchor.innerText || '').trim(),
                    href: anchor.getAttribute('href') || '',
                    onclick: anchor.getAttribute('onclick') || '',
                    dataset: { ...anchor.dataset },
                  })).filter(item =>
                    item.text ||
                    item.href.includes('item') ||
                    item.href.includes('prod') ||
                    item.onclick.includes('item') ||
                    item.onclick.includes('prod')
                  ).slice(0, 200);
                  const tables = Array.from(document.querySelectorAll('table')).slice(0, 20).map(table => ({
                    text: (table.innerText || '').trim().slice(0, 1000),
                    htmlId: table.id || '',
                    className: table.className || '',
                    headers: Array.from(table.querySelectorAll('th')).map(th => (th.innerText || '').trim()),
                    rows: Array.from(table.querySelectorAll('tr')).slice(0, 50).map(row => ({
                      text: (row.innerText || '').trim(),
                      dataset: { ...row.dataset },
                      cells: Array.from(row.querySelectorAll('th,td')).map(cell => ({
                        text: (cell.innerText || '').trim(),
                        bind: cell.getAttribute('data-bind') || '',
                        dataset: { ...cell.dataset },
                        links: Array.from(cell.querySelectorAll('a')).map(anchor => ({
                          text: (anchor.innerText || '').trim(),
                          href: anchor.getAttribute('href') || '',
                          onclick: anchor.getAttribute('onclick') || '',
                          dataset: { ...anchor.dataset },
                        })),
                        hiddenInputs: Array.from(cell.querySelectorAll('input[type="hidden"]')).map(input => ({
                          name: input.getAttribute('name') || '',
                          id: input.getAttribute('id') || '',
                          value: input.getAttribute('value') || '',
                        })),
                      })),
                    })),
                  }));
                  return {
                    url: location.href,
                    title: document.title,
                    hasCallAjaxObj: typeof callAjaxObj === 'function',
                    globals,
                    functions,
                    anchors,
                    tables,
                  };
                }
                """
            )
            self._write_debug_json("miraeasset_personal_pension_identity_probe.json", payload)
        except Exception:
            return

    def _select_retirement_account(self, page: Page, frame: Frame) -> Frame:
        retirement_account_number = self._setting("retirement_account_number")
        select_id = self.broker_config.dom.retirement_account_select_id
        options = frame.evaluate(
            f"""
            () => {{
              const select = document.querySelector('#{select_id}');
              if (!select) return [];
              return Array.from(select.options).map(option => {{
                return {{
                  value: option.value || '',
                  text: option.text || '',
                  selected: option.selected,
                }};
              }});
            }}
            """
        )
        if not options:
            return frame

        if not retirement_account_number:
            frame.wait_for_timeout(12000)
            return frame

        normalized = "".join(ch for ch in retirement_account_number if ch.isdigit())
        for option in options:
            candidate = "".join(ch for ch in option["text"] if ch.isdigit())
            if candidate != normalized:
                continue
            if option["selected"]:
                frame.wait_for_timeout(12000)
                return frame
            frame.evaluate(
                f"""
                (value) => {{
                  const select = document.querySelector('#{select_id}');
                  if (!select) {{
                    throw new Error('accountSelBox select not found');
                  }}
                  select.value = value;
                  if (typeof rpChangeReload === 'function') {{
                    rpChangeReload();
                    return;
                  }}
                  select.dispatchEvent(new Event('change', {{ bubbles: true }}));
                }}
                """,
                str(option["value"]),
            )
            page.wait_for_timeout(12000)
            refreshed = self._content_frame(page)
            refreshed.wait_for_selector(f"#{self.broker_config.dom.retirement_holdings_tbody_id}")
            return refreshed

        raise RuntimeError(f"설정한 미래에셋 퇴직연금 계좌번호를 찾지 못했습니다: {retirement_account_number}")

    def _load_all_retirement_rows(self, frame: Frame) -> None:
        tbody_selector = f"#{self.broker_config.dom.retirement_holdings_tbody_id} tr"
        more_selector = f"#{self.broker_config.dom.retirement_holdings_more_id}"
        for _ in range(10):
            visible = frame.evaluate(
                f"""
                () => {{
                  const more = document.querySelector('{more_selector}');
                  if (!more) return false;
                  return window.getComputedStyle(more).display !== 'none';
                }}
                """
            )
            if not visible:
                return
            before = frame.locator(tbody_selector).count()
            frame.evaluate("() => hkp2001a40(false)")
            frame.wait_for_timeout(1500)
            after = frame.locator(tbody_selector).count()
            if after <= before:
                return

    def _parse_retirement_holdings(self, frame: Frame, captured_at: str) -> list[AssetRecord]:
        payloads = self._fetch_retirement_holdings_payloads(frame)
        if payloads:
            self._write_debug_json("miraeasset_retirement_pension_payload_probe.json", payloads)
        records = self._parse_retirement_holdings_payloads(payloads, captured_at)
        if records:
            return records

        tbody_selector = f"#{self.broker_config.dom.retirement_holdings_tbody_id}"
        rows: list[dict[str, str]] = frame.evaluate(
            f"""
            () => {{
              const rows = Array.from(document.querySelectorAll('{tbody_selector} tr'));
              return rows.map(row => {{
                const rawCells = Array.from(row.querySelectorAll('td'));
                const cells = rawCells.map(cell => (cell.innerText || '').trim());
                const link = rawCells[0]?.querySelector('a[href*="goItemDetailInfo"], a[href*="item_cd"]');
                return {{
                  name: cells[0] || '',
                  symbol: extractItemSymbol(link?.getAttribute('href') || ''),
                  quantity: cells[2] || '',
                  evaluation_amount: cells[3] || '',
                }};
              }});

              function extractItemSymbol(value) {{
                const text = String(value || '');
                const detailMatch = text.match(/goItemDetailInfo\\(["']([^"']+)/);
                if (detailMatch) return detailMatch[1];
                const itemMatch = text.match(/[?&]item_cd=([^&"']+)/);
                return itemMatch ? decodeURIComponent(itemMatch[1]) : '';
              }}
            }}
            """
        )
        return self._build_retirement_records(rows, captured_at)

    def _fetch_retirement_holdings_payloads(self, frame: Frame) -> list[dict]:
        try:
            payloads = frame.evaluate(
                f"""
                async (url) => {{
                  if (
                    typeof callAjaxObj !== 'function' ||
                    typeof user_cont_no === 'undefined' ||
                    typeof enmn_cont_no === 'undefined'
                  ) {{
                    return [];
                  }}

                  const pages = [];
                  let next = {{
                    next_user_cont_no: '00000000000000',
                    next_enmn_cont_no: '00000000000000',
                    next_retr_ann_gd_no: '000000000000',
                  }};
                  for (let index = 0; index < 20; index += 1) {{
                    const gridState = window._rksz5300vObj || {{}};
                    const params = {{
                      tr_gb: gridState.isDefault ? 'S' : 'R',
                      user_cont_no,
                      enmn_cont_no,
                      base_date: typeof replaceAll === 'function' && typeof $ === 'function'
                        ? replaceAll($('#bsPrcBsDt').val(), '.', '')
                        : '',
                      next_user_cont_no: next.next_user_cont_no,
                      next_enmn_cont_no: next.next_enmn_cont_no,
                      next_retr_ann_gd_no: next.next_retr_ann_gd_no,
                    }};
                    const data = await new Promise((resolve) => {{
                      try {{
                        callAjaxObj({{
                          url,
                          data: params,
                          success: (payload) => resolve(payload || {{}}),
                        }});
                      }} catch (error) {{
                        resolve({{ __error__: String(error) }});
                      }}
                    }});

                    if (!data || data.__error__ || data.result === 'error') {{
                      break;
                    }}
                    pages.push(data);

                    next = {{
                      next_user_cont_no: data.next_user_cont_no || '',
                      next_enmn_cont_no: data.next_enmn_cont_no || '',
                      next_retr_ann_gd_no: data.next_retr_ann_gd_no || '',
                    }};
                    if (!next.next_retr_ann_gd_no || next.next_retr_ann_gd_no === '999999999999') {{
                      break;
                    }}
                  }}
                  return pages;
                }}
                """,
                self.broker_config.ajax.retirement_holdings,
            )
        except Exception:
            return []
        if not isinstance(payloads, list):
            return []
        return [payload for payload in payloads if isinstance(payload, dict)]

    def _parse_retirement_holdings_payloads(self, payloads: list[dict], captured_at: str) -> list[AssetRecord]:
        rows: list[dict[str, str]] = []
        for payload in payloads:
            items = payload.get("list1") or payload.get("grid01") or []
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                rows.append(
                    {
                        "name": _field(item, "retr_ann_gd_nm", "itm_nm"),
                        "symbol": _field(item, "itm_no", "asts_org_gd_c", "retr_ann_gd_no"),
                        "quantity": _field(item, "bal_pq", "hldg_q21_8", "quantity"),
                        "evaluation_amount": _field(item, "estm_amt", "ea26_8"),
                    }
                )

        return self._build_retirement_records(rows, captured_at)

    def _build_retirement_records(self, rows: list[dict[str, str]], captured_at: str) -> list[AssetRecord]:

        records: list[AssetRecord] = []
        for row in rows:
            name = clean_text(row.get("name"))
            if not name or self._is_no_data_row(row):
                continue

            asset_group = self._classify_retirement_asset_group(name)
            amount_in_krw = parse_decimal(row.get("evaluation_amount"))
            quantity = None if asset_group == "cash_equivalent" else parse_decimal(row.get("quantity"))
            if amount_in_krw is None and quantity is None and not clean_text(row.get("symbol")):
                continue
            records.append(
                AssetRecord(
                    captured_at=captured_at,
                    broker_name="miraeasset",
                    owner_name=self.account.name,
                    account_name="",
                    account_masked_id="",
                    asset_group=asset_group,
                    asset_subtype="retirement_pension",
                    market="",
                    symbol=_normalize_symbol(row.get("symbol")),
                    name=name,
                    quantity=quantity,
                    unit_currency="KRW",
                    amount_in_unit_currency=amount_in_krw,
                    fx_rate_to_krw=None,
                    amount_in_krw=amount_in_krw,
                    source_page="miraeasset_retirement_pension_holdings",
                )
            )
        return records

    def _classify_retirement_asset_group(self, name: str) -> str:
        normalized = clean_text(name)
        if normalized == "TIGER 미국나스닥100":
            return "foreign_stock"
        return "cash_equivalent"

    def _build_records(
        self,
        *,
        captured_at: str,
        rows: list[dict[str, str]],
        source_page: str,
        quantity_key: str,
        evaluation_key: str,
        asset_subtype: str,
    ) -> list[AssetRecord]:
        records: list[AssetRecord] = []
        for row in rows:
            name = clean_text(row.get("name"))
            if not name or self._is_no_data_row(row):
                continue

            quantity = parse_decimal(row.get(quantity_key))
            amount_in_krw = parse_decimal(row.get(evaluation_key))
            records.append(
                AssetRecord(
                    captured_at=captured_at,
                    broker_name="miraeasset",
                    owner_name=self.account.name,
                    account_name="",
                    account_masked_id="",
                    asset_group="foreign_stock",
                    asset_subtype=asset_subtype,
                    market="",
                    symbol=_normalize_symbol(row.get("symbol")),
                    name=name,
                    quantity=quantity,
                    unit_currency="KRW",
                    amount_in_unit_currency=amount_in_krw,
                    fx_rate_to_krw=None,
                    amount_in_krw=amount_in_krw,
                    source_page=source_page,
                )
            )
        return records

    def _is_no_data_row(self, row: dict[str, str]) -> bool:
        text = " ".join(clean_text(value) for value in row.values())
        return any(marker in text for marker in NO_DATA_MARKERS)

    def _setting(self, key: str) -> str | None:
        value = self.account.settings.get(key)
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _write_debug_json(self, filename: str, payload: object) -> None:
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        probe_path = self.debug_dir / filename
        probe_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _field(payload: dict, *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value is not None:
            return str(value)
    return ""


def _normalize_symbol(value: object) -> str:
    symbol = clean_text(value)
    if len(symbol) == 6 and symbol.isdigit():
        return f"A{symbol}"
    return symbol.upper() if symbol.isascii() else symbol


def _is_retirement_product_symbol(value: object) -> bool:
    symbol = clean_text(value)
    return len(symbol) == 12 and symbol.isdigit()

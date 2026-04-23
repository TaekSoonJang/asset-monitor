from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

from playwright.sync_api import Page, sync_playwright

from asset_monitor.config import AccountConfig
from asset_monitor.debug import save_page_debug
from asset_monitor.models import AssetRecord

from ..base import BrokerPartialCollectionError
from .config import ShinhanBrokerConfig
from .parsing import first_non_empty, parse_cash_response_payload, parse_foreign_response_payload, parse_table_html


@dataclass(slots=True)
class PartialCollectionError(BrokerPartialCollectionError):
    pass


class ShinhanCollector:
    def __init__(
        self,
        broker_config: ShinhanBrokerConfig,
        account: AccountConfig,
        debug_dir: Path,
    ) -> None:
        self.broker_config = broker_config
        self.account = account
        self.debug_dir = debug_dir
        self.selected_origin: str | None = None

    def collect(self, captured_at: str) -> dict[str, list[AssetRecord]]:
        with sync_playwright() as playwright:
            context, should_close, page, page_should_close = self._open_session(playwright)

            try:
                self._ensure_logged_in_session(page)
                account_name = self._read_first_text(page, self.broker_config.selectors.section("common").get("account_name", []))
                account_masked_id = self._read_first_text(
                    page,
                    self.broker_config.selectors.section("common").get("account_masked_id", []),
                )
                results: dict[str, list[AssetRecord]] = {"domestic": [], "foreign": [], "cash": []}
                errors: dict[str, str] = {}

                collect_map = {
                    "domestic": self.collect_domestic_positions,
                    "foreign": self.collect_foreign_positions,
                    "cash": self.collect_cash_assets,
                }

                for name in self.broker_config.asset_targets:
                    collect_fn = collect_map[name]
                    try:
                        results[name] = collect_fn(page, captured_at, account_name, account_masked_id)
                    except Exception as exc:
                        errors[name] = str(exc)
                        save_page_debug(page, self.debug_dir, f"{name}_failure")

                if errors:
                    raise PartialCollectionError(results=results, errors=errors)
                return results
            finally:
                if page_should_close:
                    try:
                        page.close()
                    except Exception:
                        pass
                if should_close:
                    context.close()

    def collect_domestic_positions(
        self,
        page: Page,
        captured_at: str,
        account_name: str,
        account_masked_id: str,
    ) -> list[AssetRecord]:
        if not self.broker_config.urls.domestic:
            return []
        page.goto(self._url_for_selected_origin(self.broker_config.urls.domestic), wait_until="domcontentloaded")
        self._run_domestic_search(page)
        return self._collect_table_page(
            page=page,
            name="domestic",
            url="",
            captured_at=captured_at,
            account_name=account_name,
            account_masked_id=account_masked_id,
            asset_group="domestic_stock",
            default_market="KRX",
            default_currency="KRW",
        )

    def collect_foreign_positions(
        self,
        page: Page,
        captured_at: str,
        account_name: str,
        account_masked_id: str,
    ) -> list[AssetRecord]:
        if not self.broker_config.urls.foreign:
            return []
        page.goto(self._url_for_selected_origin(self.broker_config.urls.foreign), wait_until="domcontentloaded")
        payload = self._fetch_foreign_positions_payload(page)
        save_page_debug(page, self.debug_dir, "foreign")
        return parse_foreign_response_payload(
            payload,
            captured_at=captured_at,
            broker_name=self.account.broker,
            owner_name=self.account.name,
            account_name=account_name,
            account_masked_id=account_masked_id,
        )

    def collect_cash_assets(
        self,
        page: Page,
        captured_at: str,
        account_name: str,
        account_masked_id: str,
    ) -> list[AssetRecord]:
        if not self.broker_config.urls.cash:
            return []
        page.goto(self._url_for_selected_origin(self.broker_config.urls.cash), wait_until="domcontentloaded")
        payload = self._fetch_cash_assets_payload(page)
        save_page_debug(page, self.debug_dir, "cash")
        return parse_cash_response_payload(
            payload,
            captured_at=captured_at,
            broker_name=self.account.broker,
            owner_name=self.account.name,
            account_name=account_name,
            account_masked_id=account_masked_id,
        )

    def _collect_table_page(
        self,
        *,
        page: Page,
        name: str,
        url: str,
        captured_at: str,
        account_name: str,
        account_masked_id: str,
        asset_group: str,
        default_market: str,
        default_currency: str,
    ) -> list[AssetRecord]:
        if url:
            page.goto(self._url_for_selected_origin(url), wait_until="domcontentloaded")
        section = self.broker_config.selectors.section(name)
        table_html = self._extract_table_html(page, section["table"])
        save_page_debug(page, self.debug_dir, name)
        return parse_table_html(
            table_html,
            captured_at=captured_at,
            broker_name=self.account.broker,
            owner_name=self.account.name,
            account_name=account_name,
            account_masked_id=account_masked_id,
            asset_group=asset_group,
            source_page=name,
            column_map=section["column_map"],
            default_market=default_market,
            default_currency=default_currency,
        )

    def _extract_table_html(self, page: Page, selectors: list[str]) -> str:
        for selector in selectors:
            try:
                candidates = page.locator(selector)
                count = candidates.count()
                for index in range(count):
                    locator = candidates.nth(index)
                    locator.wait_for(timeout=3000)
                    metrics = locator.evaluate(
                        """
                        element => ({
                          html: element.outerHTML,
                          tdCount: element.querySelectorAll('td').length,
                          trCount: element.querySelectorAll('tr').length
                        })
                        """
                    )
                    if metrics["tdCount"] > 0 and metrics["trCount"] > 1:
                        return metrics["html"]
            except Exception:
                continue

        for selector in selectors:
            locator = page.locator(selector).first
            try:
                locator.wait_for(timeout=3000)
                return locator.evaluate("element => element.outerHTML")
            except Exception:
                continue
        save_page_debug(page, self.debug_dir, "missing_table")
        raise RuntimeError(f"Could not find table using selectors: {selectors}")

    def _read_first_text(self, page: Page, selectors: list[str]) -> str:
        values: list[str] = []
        for selector in selectors:
            locator = page.locator(selector).first
            try:
                locator.wait_for(timeout=1000)
                values.append(locator.inner_text())
            except Exception:
                continue
        return first_non_empty(values)

    def _ensure_logged_in_session(self, page: Page) -> None:
        if not self.broker_config.urls.domestic:
            return

        failures: list[str] = []
        for index, url in enumerate(self._url_candidates(self.broker_config.urls.domestic), start=1):
            try:
                page.goto(url, wait_until="domcontentloaded")
                page.wait_for_timeout(2000)
                if self._looks_like_authenticated_asset_page(page):
                    parsed = urlparse(page.url)
                    self.selected_origin = f"{parsed.scheme}://{parsed.netloc}"
                    save_page_debug(page, self.debug_dir, "session_probe")
                    return
                failures.append(f"{url} -> {page.url}")
                save_page_debug(page, self.debug_dir, f"session_probe_candidate_{index}")
            except Exception as exc:
                failures.append(f"{url} -> {exc}")

        save_page_debug(page, self.debug_dir, "session_probe")
        attempted = "; ".join(failures)
        raise RuntimeError(
            f"Could not find a logged-in session for '{self.account.name}'. Attempted URLs: {attempted}"
        )

    def _looks_like_authenticated_asset_page(self, page: Page) -> bool:
        try:
            return bool(
                page.evaluate(
                    """
                    () => {
                      const text = document.body ? document.body.innerText : '';
                      const hasAccountSearch =
                        !!document.querySelector('#acct-no-combobox') &&
                        !!document.querySelector('#search-btn');
                      const hasLoginForm =
                        !!document.querySelector('#userID1') ||
                        !!document.querySelector('#userPW1') ||
                        location.href.includes('/siw/etc/login/');
                      return hasAccountSearch && !hasLoginForm && text.includes('로그아웃');
                    }
                    """
                )
            )
        except Exception:
            return False

    def _url_for_selected_origin(self, url: str) -> str:
        if not self.selected_origin:
            return url
        parsed_url = urlparse(url)
        parsed_origin = urlparse(self.selected_origin)
        return urlunparse(parsed_url._replace(scheme=parsed_origin.scheme, netloc=parsed_origin.netloc))

    def _url_candidates(self, url: str) -> list[str]:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return [url]

        candidates = [url]
        hostname = parsed.hostname or ""
        if hostname == "www.shinhansec.com":
            candidates.append(urlunparse(parsed._replace(netloc="shinhansec.com")))
        elif hostname == "shinhansec.com":
            candidates.append(urlunparse(parsed._replace(netloc="www.shinhansec.com")))

        unique_candidates: list[str] = []
        for candidate in candidates:
            if candidate not in unique_candidates:
                unique_candidates.append(candidate)
        return unique_candidates

    def _open_session(self, playwright) -> tuple[Any, bool, Page, bool]:
        browser = playwright.chromium.connect_over_cdp(self.account.cdp_url)
        context = browser.contexts[0] if browser.contexts else browser.new_context(locale="ko-KR")
        page = context.new_page()
        page.set_default_timeout(15000)
        page.set_default_navigation_timeout(30000)
        return context, False, page, True

    def _run_domestic_search(self, page: Page) -> None:
        domestic_account_number = self._require_setting("domestic_account_number")
        page.locator("#acct-no-combobox").click(timeout=10000)
        page.wait_for_timeout(800)
        page.locator(f"text={domestic_account_number}").first.click(timeout=10000)
        page.wait_for_timeout(800)
        self._trigger_search(page)

    def _fetch_foreign_positions_payload(self, page: Page) -> dict[str, Any]:
        password = self._setting("account_inquiry_password") or self._read_input_value(page, "#inq_pw")
        if not password:
            raise RuntimeError("Missing account inquiry password for foreign holdings page.")

        payload = page.evaluate(
            """
            async ({ accountNo, password }) => {
              const now = new Date();
              const pad = value => String(value).padStart(2, '0');
              const sdt =
                `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}` +
                `${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}` +
                `${String(now.getMilliseconds()).padStart(3, '0')}`;
              const body = {
                header: {
                  TCD: 'S',
                  SDT: sdt,
                  SVW: '/siw/myasset/balance/380502/view.do',
                },
                body: {
                  accountNo,
                  pwd: password,
                  gubun: window.$vo?.vl?.gubn?.() ?? '0',
                },
              };

              const response = await fetch(`/siw/myasset/balance/380502/data.do?v=${Date.now()}`, {
                method: 'POST',
                headers: {
                  'Content-Type': 'application/json;charset=UTF-8',
                },
                credentials: 'same-origin',
                body: JSON.stringify(body),
              });

              const text = await response.text();
              let json;
              try {
                json = JSON.parse(text);
              } catch (error) {
                json = { __parse_error__: String(error), __raw_text__: text };
              }

              return {
                status: response.status,
                data: json,
              };
            }
            """,
            {
                "accountNo": self._require_setting("domestic_account_number").replace("-", ""),
                "password": password,
            },
        )

        if payload["status"] != 200:
            raise RuntimeError(f"Foreign data request failed with HTTP {payload['status']}")

        data = payload["data"]
        if not isinstance(data, dict):
            raise RuntimeError("Foreign data response was not a JSON object.")

        header = data.get("header") or {}
        response_code = header.get("RCD")
        if response_code and response_code != "00000":
            message = header.get("MSG") or f"Foreign data request failed with RCD={response_code}"
            raise RuntimeError(str(message))

        return data

    def _fetch_cash_assets_payload(self, page: Page) -> dict[str, Any]:
        password = self._setting("account_inquiry_password") or self._read_input_value(page, "#inq_pw")
        if not password:
            raise RuntimeError("Missing account inquiry password for cash assets page.")

        payload = page.evaluate(
            """
            async ({ accountNo, password }) => {
              const body = {
                acctNo: accountNo,
                goodsGubn: '01',
                dataGubn: '01',
                checkForAccount: accountNo,
                pwd: password,
              };

              const response = await fetch(`/siw/myasset/balance/580001/data.do?v=${Date.now()}`, {
                method: 'POST',
                headers: {
                  'Content-Type': 'application/json;charset=UTF-8',
                },
                credentials: 'same-origin',
                body: JSON.stringify(body),
              });

              const text = await response.text();
              let json;
              try {
                json = JSON.parse(text);
              } catch (error) {
                json = { __parse_error__: String(error), __raw_text__: text };
              }

              return {
                status: response.status,
                data: json,
              };
            }
            """,
            {
                "accountNo": self._require_setting("domestic_account_number").replace("-", ""),
                "password": password,
            },
        )

        if payload["status"] != 200:
            raise RuntimeError(f"Cash data request failed with HTTP {payload['status']}")

        data = payload["data"]
        if not isinstance(data, dict):
            raise RuntimeError("Cash data response was not a JSON object.")

        header = data.get("header") or {}
        response_code = header.get("RCD")
        if response_code and response_code != "00000":
            message = header.get("MSG") or f"Cash data request failed with RCD={response_code}"
            raise RuntimeError(str(message))

        body = data.get("body") or {}
        if str(body.get("ErrType", "0")) != "0":
            raise RuntimeError(str(body.get("errorMsg") or "Cash data request returned an error."))

        return data

    def _read_input_value(self, page: Page, selector: str) -> str:
        try:
            return page.locator(selector).first.input_value(timeout=2000).strip()
        except Exception:
            return ""

    def _trigger_search(self, page: Page) -> None:
        button = page.locator("#search-btn").first
        button.wait_for(timeout=10000)
        button.scroll_into_view_if_needed(timeout=5000)

        try:
            button.click(timeout=10000)
        except Exception:
            try:
                button.click(timeout=10000, force=True)
            except Exception:
                page.evaluate(
                    """
                    () => {
                      const button = document.querySelector('#search-btn');
                      if (!button) {
                        throw new Error('Search button not found');
                      }
                      button.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
                      button.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
                      button.dispatchEvent(new MouseEvent('click', { bubbles: true }));
                    }
                    """
                )

        self._wait_for_search_completion(page)

    def _wait_for_search_completion(self, page: Page) -> None:
        loading_selectors = (
            ".loading",
            ".loader",
            ".spinner",
            ".ui-loading",
            "img[alt*='로딩']",
            "img[src*='loading']",
        )

        saw_loading = False
        for selector in loading_selectors:
            try:
                page.locator(selector).first.wait_for(state="visible", timeout=1500)
                saw_loading = True
                break
            except Exception:
                continue

        if saw_loading:
            for selector in loading_selectors:
                try:
                    page.locator(selector).first.wait_for(state="hidden", timeout=10000)
                    break
                except Exception:
                    continue

        page.wait_for_load_state("networkidle", timeout=10000)
        page.wait_for_timeout(1500)

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

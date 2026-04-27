from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from asset_monitor.config import AccountConfig
from asset_monitor.models import AssetRecord

from .config import UpbitBrokerConfig


class UpbitCollector:
    def __init__(
        self,
        broker_config: UpbitBrokerConfig,
        account: AccountConfig,
        debug_dir: Path,
    ) -> None:
        self.broker_config = broker_config
        self.account = account
        self.debug_dir = debug_dir

    def collect(self, captured_at: str) -> dict[str, list[AssetRecord]]:
        access_key = self._require_setting("access_key")
        secret_key = self._require_setting("secret_key")
        min_amount_krw = self._decimal_setting("min_amount_krw", Decimal("10000"))
        account_name = self._setting("account_name") or "Upbit"

        balances = self._get_accounts(access_key, secret_key)
        currencies = [
            str(item.get("currency") or "").strip().upper()
            for item in balances
            if str(item.get("currency") or "").strip().upper() != "KRW"
        ]
        prices = self._get_krw_ticker_prices(currencies)
        records = build_upbit_records(
            balances,
            prices,
            captured_at=captured_at,
            owner_name=self.account.name,
            account_name=account_name,
            min_amount_krw=min_amount_krw,
        )
        if not records["cash"] and not records["foreign"]:
            raise RuntimeError("Upbit returned no assets above the configured minimum amount.")
        return records

    def _get_accounts(self, access_key: str, secret_key: str) -> list[dict]:
        token = _build_jwt(access_key, secret_key)
        payload = self._request_json(
            "/v1/accounts",
            headers={"Authorization": f"Bearer {token}"},
        )
        if not isinstance(payload, list):
            raise RuntimeError("Unexpected Upbit accounts response.")
        return [item for item in payload if isinstance(item, dict)]

    def _get_krw_ticker_prices(self, currencies: list[str]) -> dict[str, Decimal]:
        unique = sorted({currency for currency in currencies if currency})
        markets = self._get_supported_krw_markets()
        prices: dict[str, Decimal] = {}
        for index in range(0, len(unique), 100):
            batch = unique[index : index + 100]
            ticker_markets = [f"KRW-{currency}" for currency in batch if f"KRW-{currency}" in markets]
            if not ticker_markets:
                continue
            payload = self._request_json("/v1/ticker", query={"markets": ",".join(ticker_markets)})
            if not isinstance(payload, list):
                continue
            for item in payload:
                if not isinstance(item, dict):
                    continue
                market = str(item.get("market") or "")
                if not market.startswith("KRW-"):
                    continue
                price = _parse_decimal(item.get("trade_price"))
                if price is not None:
                    prices[market.removeprefix("KRW-")] = price
        return prices

    def _get_supported_krw_markets(self) -> set[str]:
        try:
            payload = self._request_json("/v1/market/all")
        except HTTPError:
            return set()
        if not isinstance(payload, list):
            return set()
        return {
            str(item.get("market") or "")
            for item in payload
            if isinstance(item, dict) and str(item.get("market") or "").startswith("KRW-")
        }

    def _request_json(
        self,
        path: str,
        *,
        query: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ):
        query_string = urlencode(query or {})
        url = f"{self.broker_config.api_base_url}{path}"
        if query_string:
            url = f"{url}?{query_string}"
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "asset-monitor/0.1",
                **(headers or {}),
            },
        )
        with urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))

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

    def _decimal_setting(self, key: str, default: Decimal) -> Decimal:
        value = self._setting(key)
        if not value:
            return default
        parsed = _parse_decimal(value)
        if parsed is None:
            raise RuntimeError(f"Account '{self.account.name}' has invalid decimal setting '{key}'.")
        return parsed


def build_upbit_records(
    balances: list[dict],
    prices: dict[str, Decimal],
    *,
    captured_at: str,
    owner_name: str,
    account_name: str,
    min_amount_krw: Decimal,
) -> dict[str, list[AssetRecord]]:
    cash: list[AssetRecord] = []
    crypto: list[AssetRecord] = []

    for item in balances:
        currency = str(item.get("currency") or "").strip().upper()
        if not currency:
            continue
        balance = _parse_decimal(item.get("balance")) or Decimal("0")
        locked = _parse_decimal(item.get("locked")) or Decimal("0")
        quantity = balance + locked
        if quantity <= 0:
            continue

        if currency == "KRW":
            amount_krw = quantity
            if amount_krw < min_amount_krw:
                continue
            cash.append(
                AssetRecord(
                    captured_at=captured_at,
                    broker_name="upbit",
                    owner_name=owner_name,
                    account_name=account_name,
                    account_masked_id="",
                    asset_group="cash_equivalent",
                    asset_subtype="krw_cash",
                    market="Upbit",
                    symbol="KRW",
                    name="KRW",
                    quantity=quantity,
                    unit_currency="KRW",
                    amount_in_unit_currency=amount_krw,
                    fx_rate_to_krw=None,
                    amount_in_krw=amount_krw,
                    source_page="upbit_accounts",
                )
            )
            continue

        price = prices.get(currency)
        if price is None:
            continue
        amount_krw = quantity * price
        if amount_krw < min_amount_krw:
            continue
        crypto.append(
            AssetRecord(
                captured_at=captured_at,
                broker_name="upbit",
                owner_name=owner_name,
                account_name=account_name,
                account_masked_id="",
                asset_group="crypto_asset",
                asset_subtype="",
                market="Upbit",
                symbol=currency,
                name=currency,
                quantity=quantity,
                unit_currency="KRW",
                amount_in_unit_currency=amount_krw,
                fx_rate_to_krw=None,
                amount_in_krw=amount_krw,
                source_page="upbit_accounts",
            )
        )

    return {"domestic": [], "foreign": crypto, "cash": cash}


def _parse_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _build_jwt(access_key: str, secret_key: str) -> str:
    header = {"alg": "HS512", "typ": "JWT"}
    payload = {"access_key": access_key, "nonce": str(uuid.uuid4())}
    signing_input = f"{_base64url_json(header)}.{_base64url_json(payload)}"
    signature = hmac.new(
        secret_key.encode("utf-8"),
        signing_input.encode("utf-8"),
        hashlib.sha512,
    ).digest()
    return f"{signing_input}.{_base64url_bytes(signature)}"


def _base64url_json(payload: dict) -> str:
    return _base64url_bytes(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    )


def _base64url_bytes(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")

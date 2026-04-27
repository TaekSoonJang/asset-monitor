from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


@dataclass(slots=True)
class AccountConfig:
    broker: str
    name: str
    cdp_url: str
    profile_name: str | None = None
    settings: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AppConfig:
    spreadsheet_id: str
    asset_targets: tuple[str, ...]
    timezone: str
    debug_output_dir: Path
    logs_dir: Path
    lock_file: Path
    google_service_account_info: dict
    broker_settings: dict[str, dict[str, Any]]
    accounts: tuple[AccountConfig, ...]


def load_config() -> AppConfig:
    load_dotenv()

    asset_targets = _parse_asset_targets(os.getenv("ASSET_TARGETS", "domestic"))
    google_service_account_info = _load_service_account()
    accounts = _load_accounts()

    return AppConfig(
        spreadsheet_id=_require_env("GOOGLE_SPREADSHEET_ID"),
        asset_targets=asset_targets,
        timezone=os.getenv("TIMEZONE", "Asia/Seoul"),
        debug_output_dir=Path(os.getenv("DEBUG_OUTPUT_DIR", "artifacts/debug")),
        logs_dir=Path(os.getenv("LOGS_DIR", "logs")),
        lock_file=Path(os.getenv("LOCK_FILE", ".asset-monitor.lock")),
        google_service_account_info=google_service_account_info,
        broker_settings=_load_broker_settings(asset_targets, accounts),
        accounts=accounts,
    )


def _load_accounts() -> tuple[AccountConfig, ...]:
    config_path = _require_env("ACCOUNTS_CONFIG_PATH")
    payload = json.loads(Path(config_path).read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload:
        raise ValueError("ACCOUNTS_CONFIG_PATH must point to a JSON array with at least one account.")
    accounts: list[AccountConfig] = []
    for index, item in enumerate(payload, start=1):
        accounts.extend(_parse_account_entries(item, index))
    return tuple(accounts)


def _parse_account_entries(payload: object, index: int) -> list[AccountConfig]:
    if not isinstance(payload, dict):
        raise ValueError(f"Account entry #{index} must be an object.")

    name = str(payload.get("name") or "").strip()
    cdp_url = str(payload.get("cdp_url") or "").strip()
    profile_name = str(payload.get("profile_name") or "").strip() or None

    if not name:
        raise ValueError(f"Account entry #{index} is missing 'name'.")
    if not cdp_url:
        raise ValueError(f"Account entry #{index} is missing 'cdp_url'.")

    brokers_payload = payload.get("brokers")
    if brokers_payload is not None:
        return _parse_multi_broker_accounts(
            brokers_payload=brokers_payload,
            index=index,
            name=name,
            cdp_url=cdp_url,
            profile_name=profile_name,
        )

    broker = str(payload.get("broker") or "shinhan").strip().lower()
    settings = payload.get("settings")
    if settings is None:
        settings = _legacy_account_settings(payload)
    normalized_settings = _normalize_settings_dict(settings, index)

    return [
        AccountConfig(
            broker=broker,
            name=name,
            cdp_url=cdp_url,
            profile_name=profile_name,
            settings=normalized_settings,
        )
    ]


def _parse_multi_broker_accounts(
    *,
    brokers_payload: object,
    index: int,
    name: str,
    cdp_url: str,
    profile_name: str | None,
) -> list[AccountConfig]:
    if not isinstance(brokers_payload, dict) or not brokers_payload:
        raise ValueError(f"Account entry #{index} has an invalid 'brokers' object.")

    all_broker_settings = {
        str(key).strip().lower(): value
        for key, value in brokers_payload.items()
        if isinstance(value, dict)
    }
    if not all_broker_settings:
        raise ValueError(f"Account entry #{index} must contain at least one broker under 'brokers'.")

    accounts: list[AccountConfig] = []
    for broker, broker_payload in all_broker_settings.items():
        normalized_settings = _normalize_settings_dict(broker_payload, index)
        normalized_settings["_all_broker_settings"] = all_broker_settings
        accounts.append(
            AccountConfig(
                broker=broker,
                name=name,
                cdp_url=cdp_url,
                profile_name=profile_name,
                settings=normalized_settings,
            )
        )
    return accounts


def _load_service_account() -> dict:
    raw_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if raw_json:
        return json.loads(raw_json)

    file_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    if not file_path:
        raise ValueError("Either GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_SERVICE_ACCOUNT_FILE must be set.")
    return json.loads(Path(file_path).read_text(encoding="utf-8"))


def _load_broker_settings(
    asset_targets: tuple[str, ...],
    accounts: tuple[AccountConfig, ...],
) -> dict[str, dict[str, Any]]:
    brokers = {account.broker for account in accounts}
    settings: dict[str, dict[str, Any]] = {}

    if "shinhan" in brokers:
        settings["shinhan"] = {
            "selector_config_path": os.getenv("SELECTOR_CONFIG_PATH"),
        }

    if "miraeasset" in brokers:
        settings["miraeasset"] = {
            "routes": {
                "account_assets_url": os.getenv(
                    "MIRAEASSET_ACCOUNT_ASSETS_URL",
                    "https://securities.miraeasset.com/hkd/hkd1002/r01.do?acno=",
                ),
                "personal_pension_balance_url": os.getenv(
                    "MIRAEASSET_PENSION_BALANCE_URL",
                    "https://securities.miraeasset.com/hkp/hkp1002/r01.do",
                ),
                "retirement_pension_balance_url": os.getenv(
                    "MIRAEASSET_RETIREMENT_PENSION_BALANCE_URL",
                    "https://securities.miraeasset.com/hkp/hkp2001/r01.do",
                ),
            }
        }

    if "kiwoom" in brokers:
        settings["kiwoom"] = {
            "routes": {
                "domestic_url": os.getenv(
                    "KIWOOM_DOMESTIC_URL",
                    "https://www1.kiwoom.com/h/mykiwoom/asset/VTotalBalanceDomesticView",
                ),
                "foreign_url": os.getenv(
                    "KIWOOM_FOREIGN_URL",
                    "https://www1.kiwoom.com/h/mykiwoom/asset/VTotalBalanceForeignView",
                ),
            }
        }

    return settings


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def _parse_asset_targets(raw_value: str) -> tuple[str, ...]:
    allowed = {"domestic", "foreign", "cash"}
    targets = tuple(part.strip().lower() for part in raw_value.split(",") if part.strip())
    if not targets:
        return ("domestic",)
    invalid = [target for target in targets if target not in allowed]
    if invalid:
        raise ValueError(f"Unsupported ASSET_TARGETS value(s): {', '.join(invalid)}")
    return targets


def _normalize_settings_dict(settings: object, index: int) -> dict[str, Any]:
    if not isinstance(settings, dict):
        raise ValueError(f"Account entry #{index} has an invalid settings object.")
    return {str(key): value for key, value in settings.items()}


def _legacy_account_settings(payload: dict[str, Any]) -> dict[str, Any]:
    settings: dict[str, Any] = {}
    domestic_account_number = str(payload.get("domestic_account_number") or "").strip()
    if domestic_account_number:
        settings["domestic_account_number"] = domestic_account_number

    account_inquiry_password = payload.get("account_inquiry_password")
    if account_inquiry_password is not None:
        text = str(account_inquiry_password).strip()
        if text:
            settings["account_inquiry_password"] = text

    return settings

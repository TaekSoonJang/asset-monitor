from __future__ import annotations

from dataclasses import dataclass

from asset_monitor.config import AppConfig


@dataclass(slots=True)
class UpbitBrokerConfig:
    api_base_url: str


def load_upbit_config(config: AppConfig) -> UpbitBrokerConfig:
    settings = config.broker_settings.get("upbit", {})
    return UpbitBrokerConfig(
        api_base_url=str(settings.get("api_base_url") or "https://api.upbit.com").rstrip("/"),
    )


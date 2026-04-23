from __future__ import annotations

from dataclasses import dataclass

from asset_monitor.config import AppConfig
from asset_monitor.selectors import SelectorConfig


@dataclass(slots=True)
class ShinhanUrlConfig:
    domestic: str
    foreign: str
    cash: str


@dataclass(slots=True)
class ShinhanBrokerConfig:
    urls: ShinhanUrlConfig
    asset_targets: tuple[str, ...]
    selectors: SelectorConfig


def load_shinhan_config(config: AppConfig) -> ShinhanBrokerConfig:
    settings = config.broker_settings.get("shinhan", {})
    return ShinhanBrokerConfig(
        urls=ShinhanUrlConfig(
            domestic="https://shinhansec.com/siw/myasset/balance/540101/view.do",
            foreign="https://shinhansec.com/siw/myasset/balance/380502/view.do",
            cash="https://shinhansec.com/siw/myasset/balance/580001/view.do",
        ),
        asset_targets=config.asset_targets,
        selectors=SelectorConfig.load(settings.get("selector_config_path")),
    )

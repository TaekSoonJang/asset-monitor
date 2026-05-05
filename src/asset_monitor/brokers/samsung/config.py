from __future__ import annotations

from dataclasses import dataclass

from asset_monitor.config import AppConfig


@dataclass(slots=True)
class SamsungRouteConfig:
    main_url: str
    type_status_url: str


@dataclass(slots=True)
class SamsungBrokerConfig:
    routes: SamsungRouteConfig


def load_samsung_config(config: AppConfig) -> SamsungBrokerConfig:
    settings = config.broker_settings.get("samsung", {})
    routes = settings.get("routes") or {}
    return SamsungBrokerConfig(
        routes=SamsungRouteConfig(
            main_url=routes.get(
                "main_url",
                "https://www.samsungpop.com/ux/kor/main/my/main.do",
            ),
            type_status_url=routes.get(
                "type_status_url",
                "https://www.samsungpop.com/ux/kor/main/my/getTypeStatus.do",
            ),
        )
    )

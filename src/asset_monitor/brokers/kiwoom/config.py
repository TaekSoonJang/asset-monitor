from __future__ import annotations

from dataclasses import dataclass

from asset_monitor.config import AppConfig


@dataclass(slots=True)
class KiwoomRouteConfig:
    domestic_url: str
    foreign_url: str


@dataclass(slots=True)
class KiwoomDomConfig:
    account_select_id: str
    password_input_id: str
    domestic_search_button_id: str
    foreign_search_button_id: str


@dataclass(slots=True)
class KiwoomBrokerConfig:
    routes: KiwoomRouteConfig
    dom: KiwoomDomConfig


def load_kiwoom_config(config: AppConfig) -> KiwoomBrokerConfig:
    settings = config.broker_settings.get("kiwoom", {})
    routes = settings.get("routes") or {}
    return KiwoomBrokerConfig(
        routes=KiwoomRouteConfig(
            domestic_url=routes.get(
                "domestic_url",
                "https://www1.kiwoom.com/h/mykiwoom/asset/VTotalBalanceDomesticView",
            ),
            foreign_url=routes.get(
                "foreign_url",
                "https://www1.kiwoom.com/h/mykiwoom/asset/VTotalBalanceForeignView",
            ),
        ),
        dom=KiwoomDomConfig(
            account_select_id="acnt_no",
            password_input_id="pswd",
            domestic_search_button_id="btn_search",
            foreign_search_button_id="btnSearch",
        ),
    )

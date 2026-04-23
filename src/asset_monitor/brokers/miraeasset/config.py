from __future__ import annotations

from dataclasses import dataclass

from asset_monitor.config import AppConfig


@dataclass(slots=True)
class MiraeAssetRouteConfig:
    account_assets_url: str
    personal_pension_balance_url: str
    retirement_pension_balance_url: str


@dataclass(slots=True)
class MiraeAssetDomConfig:
    content_frame_name: str
    account_list_tbody_id: str
    account_holdings_tbody_id: str
    account_holdings_more_id: str
    pension_holdings_tbody_id: str
    retirement_account_select_id: str
    retirement_holdings_tbody_id: str
    retirement_holdings_more_id: str


@dataclass(slots=True)
class MiraeAssetAjaxConfig:
    account_list: str
    account_holdings: str
    pension_holdings: str
    retirement_holdings: str


@dataclass(slots=True)
class MiraeAssetBrokerConfig:
    routes: MiraeAssetRouteConfig
    dom: MiraeAssetDomConfig
    ajax: MiraeAssetAjaxConfig


def load_miraeasset_config(config: AppConfig) -> MiraeAssetBrokerConfig:
    settings = config.broker_settings.get("miraeasset", {})
    routes = settings.get("routes") or {}
    return MiraeAssetBrokerConfig(
        routes=MiraeAssetRouteConfig(
            account_assets_url=routes.get("account_assets_url", "https://securities.miraeasset.com/hkd/hkd1002/r01.do?acno="),
            personal_pension_balance_url=routes.get("personal_pension_balance_url", "https://securities.miraeasset.com/hkp/hkp1002/r01.do"),
            retirement_pension_balance_url=routes.get(
                "retirement_pension_balance_url",
                "https://securities.miraeasset.com/hkp/hkp2001/r01.do",
            ),
        ),
        dom=MiraeAssetDomConfig(
            content_frame_name="contentframe",
            account_list_tbody_id="hkd1002a01ListTbody",
            account_holdings_tbody_id="hkd1002a02ListTbody",
            account_holdings_more_id="moreList",
            pension_holdings_tbody_id="hkp1002a24ListView",
            retirement_account_select_id="accountSelBox",
            retirement_holdings_tbody_id="dataGrid_rksz5300v",
            retirement_holdings_more_id="moreview_rksz5300v",
        ),
        ajax=MiraeAssetAjaxConfig(
            account_list="/banking/getMyAccountListData.json",
            account_holdings="/hkd/hkd1002/a01.json",
            pension_holdings="/hkp/hkp1002/a24.json",
            retirement_holdings="/hkp/hkp2001/a40.json",
        ),
    )

from __future__ import annotations

from pathlib import Path

from asset_monitor.config import AccountConfig, AppConfig

from .base import BrokerCollector
from .miraeasset.config import load_miraeasset_config
from .miraeasset.collector import MiraeAssetCollector
from .shinhan.collector import ShinhanCollector
from .shinhan.config import load_shinhan_config


def create_broker_collector(config: AppConfig, account: AccountConfig, debug_dir: Path) -> BrokerCollector:
    broker = account.broker.lower()
    if broker == "shinhan":
        return ShinhanCollector(
            broker_config=load_shinhan_config(config),
            account=account,
            debug_dir=debug_dir,
        )
    if broker == "miraeasset":
        return MiraeAssetCollector(
            broker_config=load_miraeasset_config(config),
            account=account,
            debug_dir=debug_dir,
        )
    raise ValueError(f"Unsupported broker: {account.broker}")

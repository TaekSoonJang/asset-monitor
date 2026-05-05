from __future__ import annotations

from pathlib import Path

from asset_monitor.config import AccountConfig, AppConfig

from .base import BrokerCollector
from .kiwoom.collector import KiwoomCollector
from .kiwoom.config import load_kiwoom_config
from .miraeasset.config import load_miraeasset_config
from .miraeasset.collector import MiraeAssetCollector
from .samsung.collector import SamsungCollector
from .samsung.config import load_samsung_config
from .shinhan.collector import ShinhanCollector
from .shinhan.config import load_shinhan_config
from .upbit.collector import UpbitCollector
from .upbit.config import load_upbit_config


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
    if broker == "kiwoom":
        return KiwoomCollector(
            broker_config=load_kiwoom_config(config),
            account=account,
            debug_dir=debug_dir,
        )
    if broker == "samsung":
        return SamsungCollector(
            broker_config=load_samsung_config(config),
            account=account,
            debug_dir=debug_dir,
        )
    if broker == "upbit":
        return UpbitCollector(
            broker_config=load_upbit_config(config),
            account=account,
            debug_dir=debug_dir,
        )
    raise ValueError(f"Unsupported broker: {account.broker}")

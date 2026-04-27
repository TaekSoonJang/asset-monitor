from pathlib import Path
from types import SimpleNamespace

import pytest

from asset_monitor.brokers import miraeasset as miraeasset_module
from asset_monitor.brokers.miraeasset.collector import MiraeAssetCollector
from asset_monitor.config import AccountConfig
from asset_monitor.models import AssetRecord


class FakePage:
    def __init__(self) -> None:
        self.waits: list[int] = []

    def wait_for_timeout(self, timeout_ms: int) -> None:
        self.waits.append(timeout_ms)


def _retirement_record() -> AssetRecord:
    return AssetRecord(
        captured_at="2026-04-28T12:00:00+09:00",
        broker_name="miraeasset",
        owner_name="sunha",
        account_name="",
        account_masked_id="",
        asset_group="cash_equivalent",
        asset_subtype="retirement_pension",
        market="",
        symbol="",
        name="퇴직연금 현금성자산",
        quantity=None,
        unit_currency="KRW",
        amount_in_unit_currency=None,
        fx_rate_to_krw=None,
        amount_in_krw=None,
        source_page="miraeasset_retirement_pension_holdings",
    )


def test_retirement_pension_collection_retries_empty_holdings(monkeypatch: pytest.MonkeyPatch) -> None:
    collector = object.__new__(MiraeAssetCollector)
    collector.account = AccountConfig(
        broker="miraeasset",
        name="sunha",
        cdp_url="http://127.0.0.1:9222",
        settings={"retirement_account_number": "123-456"},
    )
    collector.broker_config = SimpleNamespace(
        routes=SimpleNamespace(retirement_pension_balance_url="https://example.test/retirement")
    )
    collector.debug_dir = Path("debug")

    page = FakePage()
    opened_frames: list[str] = []
    parsed_frames: list[str] = []

    def open_frame_path(page_arg: FakePage, url: str) -> str:
        frame = f"frame-{len(opened_frames) + 1}"
        opened_frames.append(frame)
        return frame

    def parse_retirement_holdings(frame: str, captured_at: str) -> list[AssetRecord]:
        parsed_frames.append(frame)
        if len(parsed_frames) == 1:
            return []
        return [_retirement_record()]

    collector._open_frame_path = open_frame_path
    collector._ensure_logged_in = lambda frame: None
    collector._select_retirement_account = lambda page_arg, frame: frame
    collector._load_all_retirement_rows = lambda frame: None
    collector._parse_retirement_holdings = parse_retirement_holdings
    monkeypatch.setattr(miraeasset_module.collector, "save_page_debug", lambda *args: None)

    records = collector._collect_retirement_pension_holdings(page, "2026-04-28T12:00:00+09:00")

    assert len(records) == 1
    assert opened_frames == ["frame-1", "frame-2"]
    assert parsed_frames == ["frame-1", "frame-2"]
    assert page.waits == [5000]

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


class FakeFrame:
    def __init__(self, rows: list[dict[str, str]]) -> None:
        self.rows = rows

    def evaluate(self, script: str, *args) -> list[dict[str, str]]:
        return self.rows


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
        name="retirement cash",
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


@pytest.mark.parametrize(
    "message",
    [
        "議고쉶????쒓? ?놁뒿?덈떎",
        "蹂댁쑀 ?곹뭹???놁뒿?덈떎.",
        "議고쉶 ?댁뿭???놁뒿?덈떎.",
    ],
)
def test_retirement_pension_parser_ignores_no_data_rows(message: str) -> None:
    collector = object.__new__(MiraeAssetCollector)
    collector.account = AccountConfig(
        broker="miraeasset",
        name="sunha",
        cdp_url="http://127.0.0.1:9222",
        settings={},
    )
    collector.broker_config = SimpleNamespace(
        dom=SimpleNamespace(retirement_holdings_tbody_id="retirementRows")
    )
    frame = FakeFrame(
        [
            {
                "name": message,
                "quantity": "",
                "evaluation_amount": "",
            }
        ]
    )

    records = collector._parse_retirement_holdings(frame, "2026-04-28T12:00:00+09:00")

    assert records == []


def test_account_holdings_payload_uses_item_number_as_symbol() -> None:
    collector = object.__new__(MiraeAssetCollector)
    collector.account = AccountConfig(
        broker="miraeasset",
        name="sunha",
        cdp_url="http://127.0.0.1:9222",
        settings={},
    )

    records = collector._parse_account_holdings_payloads(
        [
            {
                "grid01": [
                    {
                        "itm_no": "TSLA",
                        "itm_nm": "Tesla",
                        "hldg_q21_8": "111",
                        "ea26_8": "70525515",
                    }
                ]
            }
        ],
        "2026-04-28T12:00:00+09:00",
    )

    assert len(records) == 1
    assert records[0].symbol == "TSLA"
    assert records[0].name == "Tesla"
    assert records[0].amount_in_krw is not None
    assert records[0].amount_in_krw is not None


def test_account_holdings_payload_falls_back_to_blank_symbol_when_missing() -> None:
    collector = object.__new__(MiraeAssetCollector)
    collector.account = AccountConfig(
        broker="miraeasset",
        name="sunha",
        cdp_url="http://127.0.0.1:9222",
        settings={},
    )

    records = collector._parse_account_holdings_payloads(
        [
            {
                "grid01": [
                    {
                        "itm_nm": "Tesla",
                        "hldg_q21_8": "111",
                        "ea26_8": "70525515",
                    }
                ]
            }
        ],
        "2026-04-28T12:00:00+09:00",
    )

    assert len(records) == 1
    assert records[0].symbol == ""
    assert records[0].name == "Tesla"


def test_pension_holdings_payload_uses_item_number_as_symbol() -> None:
    collector = object.__new__(MiraeAssetCollector)
    collector.account = AccountConfig(
        broker="miraeasset",
        name="sunha",
        cdp_url="http://127.0.0.1:9222",
        settings={},
    )

    records = collector._parse_pension_holdings_payload(
        {
            "grid01": [
                {
                    "itm_no": "KR7133690008",
                    "itm_nm": "TIGER Nasdaq 100",
                    "hldg_q21_8": "157",
                    "ea26_8": "30280590",
                }
            ]
        },
        "2026-04-28T12:00:00+09:00",
    )

    assert len(records) == 1
    assert records[0].symbol == "KR7133690008"
    assert records[0].name == "TIGER Nasdaq 100"
    assert records[0].quantity is not None


def test_retirement_holdings_payload_uses_item_number_before_pension_product_code() -> None:
    collector = object.__new__(MiraeAssetCollector)
    collector.account = AccountConfig(
        broker="miraeasset",
        name="sunha",
        cdp_url="http://127.0.0.1:9222",
        settings={},
    )

    records = collector._parse_retirement_holdings_payloads(
        [
            {
                "list1": [
                    {
                        "itm_no": "133690",
                        "asts_org_gd_c": "212001000229",
                        "retr_ann_gd_nm": "TIGER Nasdaq 100",
                        "estm_amt": "1000",
                    }
                ]
            }
        ],
        "2026-04-28T12:00:00+09:00",
    )

    assert len(records) == 1
    assert records[0].symbol == "A133690"
    assert records[0].name == "TIGER Nasdaq 100"


def test_retirement_holdings_payload_falls_back_to_origin_product_code() -> None:
    collector = object.__new__(MiraeAssetCollector)
    collector.account = AccountConfig(
        broker="miraeasset",
        name="sunha",
        cdp_url="http://127.0.0.1:9222",
        settings={},
    )

    records = collector._parse_retirement_holdings_payloads(
        [
            {
                "list1": [
                    {
                        "asts_org_gd_c": "360750",
                        "retr_ann_gd_nm": "TIGER S&P500",
                        "estm_amt": "1000",
                    }
                ]
            }
        ],
        "2026-04-28T12:00:00+09:00",
    )

    assert len(records) == 1
    assert records[0].symbol == "A360750"
    assert records[0].name == "TIGER S&P500"


def test_retirement_holdings_payload_falls_back_to_retirement_product_number() -> None:
    collector = object.__new__(MiraeAssetCollector)
    collector.account = AccountConfig(
        broker="miraeasset",
        name="sunha",
        cdp_url="http://127.0.0.1:9222",
        settings={},
    )

    records = collector._parse_retirement_holdings_payloads(
        [
            {
                "list1": [
                    {
                        "retr_ann_gd_no": "212001000229",
                        "retr_ann_gd_nm": "TIGER S&P500",
                        "bal_pq": "35",
                        "estm_amt": "1000",
                    }
                ]
            }
        ],
        "2026-04-28T12:00:00+09:00",
    )

    assert len(records) == 1
    assert records[0].symbol == "212001000229"
    assert records[0].amount_in_krw is not None


def test_retirement_symbol_is_canonicalized_from_matching_non_retirement_record() -> None:
    collector = object.__new__(MiraeAssetCollector)
    records = [
        AssetRecord(
            captured_at="2026-04-28T12:00:00+09:00",
            broker_name="miraeasset",
            owner_name="sunha",
            account_name="",
            account_masked_id="",
            asset_group="foreign_stock",
            asset_subtype="personal_pension",
            market="",
            symbol="A133690",
            name="TIGER 미국나스닥100",
            quantity=None,
            unit_currency="KRW",
            amount_in_unit_currency=None,
            fx_rate_to_krw=None,
            amount_in_krw=None,
            source_page="miraeasset_personal_pension_holdings",
        ),
        AssetRecord(
            captured_at="2026-04-28T12:00:00+09:00",
            broker_name="miraeasset",
            owner_name="sunha",
            account_name="",
            account_masked_id="",
            asset_group="foreign_stock",
            asset_subtype="retirement_pension",
            market="",
            symbol="212001000229",
            name="TIGER 미국나스닥100",
            quantity=None,
            unit_currency="KRW",
            amount_in_unit_currency=None,
            fx_rate_to_krw=None,
            amount_in_krw=None,
            source_page="miraeasset_retirement_pension_holdings",
        ),
    ]

    collector._canonicalize_retirement_symbols(records)

    assert records[1].symbol == "A133690"

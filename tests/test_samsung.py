from decimal import Decimal

from asset_monitor.brokers.samsung.collector import build_samsung_records


def test_build_samsung_records_uses_hidden_quantity_field() -> None:
    records = build_samsung_records(
        {
            "result": [
                {
                    "A_UMS_MASK_ACNT_NO": "7081******-47  ",
                    "A_CLNT_TRDG_PRDT_CODE_NAME": "원화",
                    "STND_PRDT_CLSN_CODE": "N1711",
                    "ISUS_NAME": "현금잔고(예수금)",
                    "DCPN_BLNC_QNTY": "0000000000000000.000000",
                    "A_VLTN_AMNT21": "0000000000000702.00",
                    "A_BUY_AMNT": "0000000000000000.00",
                },
                {
                    "A_UMS_MASK_ACNT_NO": "7132******-01  ",
                    "A_CLNT_TRDG_PRDT_CODE_NAME": "해외주식",
                    "KRW_FRGN_CRNY_SECT_CODE": "2",
                    "ISCD": "DHER.DE",
                    "ISUS_NAME": "딜리버리 히어로",
                    "DCPN_BLNC_QNTY": "0000000000000136.000000",
                    "A_VLTN_AMNT21": "0000000004889779.00",
                    "A_BUY_AMNT": "0000000005179383.00",
                },
            ]
        },
        captured_at="2026-05-05T22:00:00+09:00",
        owner_name="sunha",
    )

    assert records["domestic"] == []
    assert len(records["cash"]) == 1
    assert records["cash"][0].broker_name == "samsung"
    assert records["cash"][0].asset_group == "cash_equivalent"
    assert records["cash"][0].quantity == Decimal("702.00")
    assert records["cash"][0].amount_in_krw == Decimal("702.00")

    assert len(records["foreign"]) == 1
    assert records["foreign"][0].broker_name == "samsung"
    assert records["foreign"][0].asset_group == "foreign_stock"
    assert records["foreign"][0].symbol == "DHER.DE"
    assert records["foreign"][0].name == "딜리버리 히어로"
    assert records["foreign"][0].quantity == Decimal("136.000000")
    assert records["foreign"][0].amount_in_krw == Decimal("4889779.00")

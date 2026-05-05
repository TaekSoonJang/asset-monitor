from decimal import Decimal

from asset_monitor.brokers.kiwoom.collector import (
    build_kiwoom_domestic_records,
    build_kiwoom_foreign_cash_records,
    build_kiwoom_foreign_records,
)


def test_build_kiwoom_domestic_records() -> None:
    rows = [
        ["종목코드", "평가손익", "매입가", "매입금액", "보유수량", "전일매수수량", "수수료", "전일종가", "신용구분"],
        ["종목명", "수익률(%)", "현재가", "평가금액", "매매가능수량", "금일매수수량", "세금", "보유비중(%)", "대출일"],
        ["A035420", "-684,040", "300,000", "2,400,000", "8", "0", "600", "215,000", ""],
        ["NAVER", "-28.5", "215,000", "1,720,000", "8", "0", "3,440", "100", ""],
    ]

    records = build_kiwoom_domestic_records(
        rows,
        captured_at="2026-04-27T12:00:00+09:00",
        owner_name="sunha",
    )

    assert len(records) == 1
    assert records[0].broker_name == "kiwoom"
    assert records[0].symbol == "A035420"
    assert records[0].name == "NAVER"
    assert records[0].quantity == Decimal("8")
    assert records[0].amount_in_krw == Decimal("1720000")


def test_build_kiwoom_foreign_records() -> None:
    rows = [
        ["종목명", "보유량", "매입가", "현재가", "평가손익", "수익률(%)", "결제잔고", "매입금액", "평가금액", "매입환율", "현재환율", "환차손익", "환평가손익", "전일", "금일", "통화", "종목코드"],
        ["S&P 500 SPDR ETF", "38", "543.3826", "713.48", "6,463.7000", "31.30%", "38", "20,648.54", "27,112.24", "1,366.74", "1,482.9", "2,398,402", "11,983,422", "0", "0", "USD", "SPY"],
        ["Tesla", "96", "351.5376", "366.84", "1,469.0304", "4.35%", "96", "33,747.6096", "35,216.64", "1,454.08", "1,482.9", "972,484", "3,150,909", "0", "0", "USD", "TSLA"],
    ]

    records = build_kiwoom_foreign_records(
        rows,
        captured_at="2026-04-27T12:00:00+09:00",
        owner_name="sunha",
    )

    assert len(records) == 2
    assert records[0].name == "S&P 500 SPDR ETF"
    assert records[0].quantity == Decimal("38")
    assert records[0].symbol == "SPY"
    assert records[0].amount_in_unit_currency == Decimal("27112.24")
    assert records[0].fx_rate_to_krw == Decimal("1482.9")
    assert records[0].amount_in_krw == Decimal("40204740.696")


def test_build_kiwoom_foreign_cash_records() -> None:
    records = build_kiwoom_foreign_cash_records(
        "33,749",
        [
            ["통화코드", "외화예수금", "해외증권평가금", "기준환율", "환전예수금", "환전평가금"],
            ["USD", "1,134.2", "73,327.1", "1,484.8", "1,684,060", "108,876,078"],
        ],
        captured_at="2026-05-05T22:00:02+09:00",
        owner_name="sunha",
    )

    assert len(records) == 2
    assert records[0].asset_group == "cash_equivalent"
    assert records[0].asset_subtype == "krw_cash"
    assert records[0].symbol == "KRW"
    assert records[0].name == "원화예수금"
    assert records[0].quantity == Decimal("33749")
    assert records[0].amount_in_krw == Decimal("33749")
    assert records[1].asset_group == "cash_equivalent"
    assert records[1].asset_subtype == "fx_cash"
    assert records[1].symbol == "USD"
    assert records[1].name == "USD 외화예수금"
    assert records[1].quantity == Decimal("1134.2")
    assert records[1].unit_currency == "USD"
    assert records[1].fx_rate_to_krw == Decimal("1484.8")
    assert records[1].amount_in_krw == Decimal("1684060")

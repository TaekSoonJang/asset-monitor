from decimal import Decimal

from asset_monitor.parsing import (
    infer_cash_subtype,
    parse_cash_response_payload,
    parse_decimal,
    parse_foreign_response_payload,
    parse_table_html,
    summarize_latest,
)


def test_parse_decimal_handles_commas() -> None:
    assert parse_decimal("1,234.56") == Decimal("1234.56")
    assert parse_decimal("-9,001") == Decimal("-9001")
    assert parse_decimal("N/A") is None


def test_parse_domestic_table_html() -> None:
    html = """
    <table>
      <tr><th>Symbol</th><th>Name</th><th>Qty</th><th>Amount</th><th>KRW</th></tr>
      <tr><td>005930</td><td>Samsung Electronics</td><td>10</td><td>550,000</td><td>550,000</td></tr>
    </table>
    """
    records = parse_table_html(
        html,
        captured_at="2026-04-22T12:00:00+09:00",
        broker_name="shinhan",
        owner_name="me",
        account_name="main",
        account_masked_id="123-45**",
        asset_group="domestic_stock",
        source_page="domestic",
        column_map={
            "symbol": 0,
            "name": 1,
            "quantity": 2,
            "amount_in_unit_currency": 3,
            "amount_in_krw": 4,
        },
        default_market="KRX",
        default_currency="KRW",
    )
    assert len(records) == 1
    assert records[0].broker_name == "shinhan"
    assert records[0].symbol == "005930"
    assert records[0].quantity == Decimal("10")
    assert records[0].amount_in_krw == Decimal("550000")


def test_parse_foreign_table_html_with_fx_fallback() -> None:
    html = """
    <table>
      <tr><th>Market</th><th>Symbol</th><th>Name</th><th>Qty</th><th>Currency</th><th>Amount</th><th>FX</th><th>KRW</th></tr>
      <tr><td>NASDAQ</td><td>AAPL</td><td>Apple</td><td>2</td><td>USD</td><td>400</td><td>1300</td><td></td></tr>
    </table>
    """
    records = parse_table_html(
        html,
        captured_at="2026-04-22T12:00:00+09:00",
        broker_name="shinhan",
        owner_name="me",
        account_name="foreign",
        account_masked_id="123-45**",
        asset_group="foreign_stock",
        source_page="foreign",
        column_map={
            "market": 0,
            "symbol": 1,
            "name": 2,
            "quantity": 3,
            "unit_currency": 4,
            "amount_in_unit_currency": 5,
            "fx_rate_to_krw": 6,
            "amount_in_krw": 7,
        },
        default_currency="USD",
    )
    assert len(records) == 1
    assert records[0].unit_currency == "USD"
    assert records[0].amount_in_krw == Decimal("520000")


def test_parse_foreign_response_payload() -> None:
    payload = {
        "body": {
            "list": [
                {
                    "ISIN코드": "AMZN",
                    "종목코드": "US0231351067",
                    "해외증권잔고수량": "59",
                    "통화코드": "USD",
                    "평가금액": "14909.30",
                    "환산환율": "1470.8",
                    "종목명": "아마존닷컴",
                    "종목영문명": "AMAZON COM INC",
                    "국가명": "미국",
                    "해외시장구분명": "미국",
                }
            ]
        }
    }

    records = parse_foreign_response_payload(
        payload,
        captured_at="2026-04-22T12:00:00+09:00",
        broker_name="shinhan",
        owner_name="me",
        account_name="foreign",
        account_masked_id="123-45**",
    )

    assert len(records) == 1
    assert records[0].market == "미국"
    assert records[0].symbol == "AMZN"
    assert records[0].name == "아마존닷컴"
    assert records[0].quantity == Decimal("59")
    assert records[0].amount_in_krw == Decimal("21928598.440")


def test_parse_cash_response_payload() -> None:
    payload = {
        "body": {
            "CMA평가금액": "146,340,653",
            "외화RP평가금액": "49,766,385",
        }
    }

    records = parse_cash_response_payload(
        payload,
        captured_at="2026-04-23T00:10:00+09:00",
        broker_name="shinhan",
        owner_name="me",
        account_name="cash",
        account_masked_id="123-45**",
    )

    assert len(records) == 2
    assert records[0].name == "CMA"
    assert records[0].amount_in_krw == Decimal("146340653")
    assert records[1].name == "외화RP"
    assert records[1].amount_in_krw == Decimal("49766385")


def test_cash_subtype_detection() -> None:
    assert infer_cash_subtype("예수금", "KRW") == "krw_cash"
    assert infer_cash_subtype("외화예수금", "USD") == "fx_cash"
    assert infer_cash_subtype("RP 매수금액", "KRW") == "rp"


def test_summarize_latest_uses_newest_snapshot() -> None:
    html_old = """
    <table>
      <tr><td>005930</td><td>Samsung Electronics</td><td>1</td><td>50000</td><td>50000</td></tr>
    </table>
    """
    html_new = """
    <table>
      <tr><td>005930</td><td>Samsung Electronics</td><td>2</td><td>100000</td><td>100000</td></tr>
    </table>
    """
    old_record = parse_table_html(
        html_old,
        captured_at="2026-04-22T12:00:00+09:00",
        broker_name="shinhan",
        owner_name="me",
        account_name="main",
        account_masked_id="123",
        asset_group="domestic_stock",
        source_page="domestic",
        column_map={"symbol": 0, "name": 1, "quantity": 2, "amount_in_unit_currency": 3, "amount_in_krw": 4},
    )[0]
    new_record = parse_table_html(
        html_new,
        captured_at="2026-04-22T13:00:00+09:00",
        broker_name="shinhan",
        owner_name="me",
        account_name="main",
        account_masked_id="123",
        asset_group="domestic_stock",
        source_page="domestic",
        column_map={"symbol": 0, "name": 1, "quantity": 2, "amount_in_unit_currency": 3, "amount_in_krw": 4},
    )[0]

    latest_rows, summary_rows = summarize_latest([old_record, new_record])
    assert len(latest_rows) == 1
    assert latest_rows[0][8] == "2"
    assert any(row[1] == "100000" for row in summary_rows if row[0].startswith("me"))

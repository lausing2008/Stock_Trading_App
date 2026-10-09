from datetime import date
from src.services.option_evidence import select_expiry_gex_row


def test_gex_order_does_not_change_selected_expiry():
    rows = [{'expiry': '2026-10-16', 'gamma_flip': 200}, {'expiry': '2026-10-09', 'gamma_flip': 100}]
    assert select_expiry_gex_row(rows, date(2026, 10, 8)) == rows[1]
    assert select_expiry_gex_row(list(reversed(rows)), date(2026, 10, 8)) == rows[1]


def test_ambiguous_gex_is_not_first_row():
    assert select_expiry_gex_row([{'gamma_flip': 200}, {'gamma_flip': 100}]) is None
    assert select_expiry_gex_row([{'expiry': '2026-10-09'}, {'expiry': '2026-10-09'}], date(2026, 10, 8)) is None

"""Tests for insider.py's compute_insider_score().

EI-DOC1: docstring previously self-contradicted ("0-100 ... negative = net selling" in the
same sentence) — matches the real max(-100.0, min(100.0, score)) clamp, same stale-range
class as congress.py's compute_congress_score. Also covers the purchase-count cluster bonus,
which multiplies (not adds to) the accumulated score — including the edge case where a
net-negative score gets MORE negative under the "bonus".
"""
from unittest.mock import patch

from src.services.insider import compute_insider_score


def _score(txns):
    with patch("src.services.insider.get_insider_for_symbol", return_value=txns):
        return compute_insider_score(stock_id=1)


def test_no_transactions_scores_zero():
    assert _score([]) == 0.0


def test_ceo_purchase_uses_role_weight_30():
    txns = [{"transaction_type": "purchase", "insider_role": "CEO"}]
    assert _score(txns) == 30


def test_director_purchase_uses_role_weight_10():
    txns = [{"transaction_type": "purchase", "insider_role": "Director"}]
    assert _score(txns) == 10


def test_unknown_role_uses_default_weight_8():
    txns = [{"transaction_type": "purchase", "insider_role": "Some Random Title"}]
    assert _score(txns) == 8.0


def test_missing_role_field_uses_default_weight_8():
    txns = [{"transaction_type": "purchase"}]
    assert _score(txns) == 8.0


def test_role_matching_is_case_insensitive_substring():
    txns = [{"transaction_type": "purchase", "insider_role": "Chief Financial Officer"}]
    assert _score(txns) == 20  # matches "chief financial"


def test_sale_weighted_at_40_percent_of_role_weight():
    txns = [{"transaction_type": "sale", "insider_role": "CEO"}]
    assert _score(txns) == -30 * 0.4


def test_ei_doc1_net_selling_produces_a_real_negative_score():
    txns = [{"transaction_type": "sale", "insider_role": "CEO"}] * 2
    assert _score(txns) == -30 * 0.4 * 2


def test_cluster_bonus_at_exactly_3_purchases_multiplies_by_1_25():
    txns = [{"transaction_type": "purchase", "insider_role": "Director"}] * 3
    assert _score(txns) == (3 * 10) * 1.25


def test_cluster_bonus_below_3_purchases_does_not_apply():
    txns = [{"transaction_type": "purchase", "insider_role": "Director"}] * 2
    assert _score(txns) == 2 * 10


def test_cluster_bonus_makes_a_net_negative_score_more_negative():
    """The multiplier applies to the ENTIRE accumulated score, including negative
    contributions from sales — if purchase_count>=3 but sales dominate, the 1.25x bonus
    pushes the (already negative) score further from zero, not toward it."""
    txns = (
        [{"transaction_type": "purchase", "insider_role": "Director"}] * 3  # +30
        + [{"transaction_type": "sale", "insider_role": "CEO"}] * 3          # -36
    )
    raw = 3 * 10 - 3 * 30 * 0.4  # 30 - 36 = -6
    assert raw < 0
    assert _score(txns) == raw * 1.25


def test_score_clamped_at_positive_100():
    txns = [{"transaction_type": "purchase", "insider_role": "CEO"}] * 10
    assert _score(txns) == 100.0


def test_score_clamped_at_negative_100():
    txns = [{"transaction_type": "sale", "insider_role": "CEO"}] * 20
    assert _score(txns) == -100.0

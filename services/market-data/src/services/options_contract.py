"""Contract terms for an option row: what one contract delivers and what its premium multiplies.

Audit R05 (2026-10-22). The flow card withheld turnover for adjusted contracts, but the chain
rows feeding the game plan, the strategy matrix and the calculator dropped the contract symbol,
so every payoff, collateral and coverage figure silently assumed 100 shares and a 100 premium
multiplier. After a split, merger or special dividend an ADJUSTED contract (OCC root differs
from the underlying, e.g. MU1 for MU) can deliver a different number of shares, or shares plus
cash, so none of those figures follows.

What this module can and cannot establish:
  * A root matching the underlying is the STANDARD case. That 100 shares are delivered and that
    the premium multiplier is 100 are still ASSUMPTIONS from the root, not a deliverable record,
    and they are separate concepts (`premium_multiplier` vs `deliverable_shares`).
  * A differing root is a NONSTANDARD contract whose terms are unknown here.
  * A missing or unreadable symbol is UNVERIFIED: nothing identifies the contract.
Only the standard case is supported for automatic payoff, collateral or coverage. The others
are refused, never priced at 100 by default.
"""
from __future__ import annotations

import re

# Weekly / PM-settled index roots that are the same 100-multiplier product as their underlying.
WEEKLY_INDEX_ROOTS = {"SPXW": "SPX", "NDXP": "NDX", "RUTW": "RUT", "VIXW": "VIX", "XSPW": "XSP"}
OCC_SYMBOL = re.compile(r"^([A-Z0-9.]{1,6})(\d{6})([CP])(\d{8})$")

STANDARD = "standard_root_100_assumed"
NONSTANDARD = "nonstandard_root_deliverable_unknown"
UNVERIFIED = "unverified_100_assumed"


# No share-deliverable or NYSE-close assumption is valid for these index products.
INDEX_ROOTS = {"SPX", "SPXW", "SPXO", "NDX", "NDXP", "RUT", "RUTW", "VIX", "VIXW", "XSP", "XSPW", "OEX", "XEO", "DJX"}


def supported_underlying(symbol) -> bool:
    sym = str(symbol or "").strip().upper()
    return bool(sym) and not sym.startswith("^") and sym not in INDEX_ROOTS


def contract_status(option_symbol, underlying) -> str:
    """One of STANDARD, NONSTANDARD or UNVERIFIED. (The UNVERIFIED label keeps its historical
    name; it means the 100 assumption cannot be checked, never that it holds.)"""
    if not option_symbol or not underlying or not isinstance(option_symbol, str):
        return UNVERIFIED
    m = OCC_SYMBOL.match(option_symbol.strip().upper())
    if not m:
        return UNVERIFIED
    root = m.group(1).replace(".", "")
    root = WEEKLY_INDEX_ROOTS.get(root, root)
    return STANDARD if root == str(underlying).upper().replace(".", "") else NONSTANDARD


def contract_terms(option_symbol, underlying) -> dict:
    """The terms a payoff calculation may use, or the reason it may not."""
    status = contract_status(option_symbol, underlying)
    supported = status == STANDARD and supported_underlying(underlying)
    return {
        "status": status,
        "option_symbol": option_symbol if isinstance(option_symbol, str) else None,
        # Both None when unsupported: an unknown contract has no multiplier to assume.
        "premium_multiplier": 100 if supported else None,
        "deliverable_shares": 100 if supported else None,
        "basis": ("unsupported index product: settlement and deliverable rules are not implemented"
                  if underlying and not supported_underlying(underlying) else
                  "assumed from a root matching the underlying; not an exchange deliverable record"
                  if supported else
                  "adjusted contract: root differs from the underlying, deliverable unknown"
                  if status == NONSTANDARD else
                  "no readable contract symbol: contract terms cannot be identified"),
        "automatic_payoff_supported": supported,
    }


def is_supported(row: dict | None) -> bool:
    """True only for a row that carries supported terms. A row with no terms is refused."""
    terms = (row or {}).get("contract_terms") or {}
    return terms.get("automatic_payoff_supported") is True and terms.get("premium_multiplier") == 100


def split_supported(rows: list[dict] | None) -> tuple[list[dict], dict]:
    """(rows supported for automatic payoff, {status: count} of the refused)."""
    kept, refused = [], {}
    for r in rows or []:
        if is_supported(r):
            kept.append(r)
        else:
            st = ((r or {}).get("contract_terms") or {}).get("status", "no_contract_terms")
            refused[st] = refused.get(st, 0) + 1
    return kept, refused


def checked_contract_terms(option_symbol, underlying, *, strike, expiry=None, right=None) -> dict:
    """Check row fields against the encoded identity, not an independent listing record."""
    from datetime import datetime
    from decimal import Decimal, InvalidOperation

    terms = contract_terms(option_symbol, underlying)
    if not terms["automatic_payoff_supported"]:
        return terms
    match = OCC_SYMBOL.fullmatch(option_symbol.strip().upper())
    mismatch = False
    try:
        encoded_expiry = datetime.strptime(match.group(2), "%y%m%d").date().isoformat()
        value = Decimal(str(strike))
        mismatch = not value.is_finite() or value <= 0 or value != Decimal(match.group(4)) / 1000
        if expiry is not None:
            mismatch |= str(expiry) != encoded_expiry
        if right is not None:
            mismatch |= str(right).lower() not in ({"c", "call"} if match.group(3) == "C" else {"p", "put"})
    except (InvalidOperation, ValueError, TypeError, AttributeError):
        mismatch = True
    if mismatch:
        return {**terms, "status": "contract_identity_mismatch", "automatic_payoff_supported": False,
                "premium_multiplier": None, "deliverable_shares": None,
                "basis": "row strike, expiry or side disagrees with the encoded contract identity"}
    return terms

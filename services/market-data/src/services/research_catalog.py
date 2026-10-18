"""Unapproved starter catalogue, checked against primary filings October 17, 2026.
Review expiry is a catalogue policy, not a claim that the commercial relationship expires.
Import never approves an edge or publishes a connection.
"""

COMMON = {
    "source_symbol": "TSM",
    "relation": "supplier",
    "valid_from": "2026-10-17",
    "valid_to": "2027-01-15",
    "keywords": ["capacity", "wafer", "packaging", "guidance"],
    "horizon_days": 20,
    "direction": "watch",
    "mechanism": "TSMC capacity or delivery changes may affect the customer's ability to supply products. This is an inference; quantify exposure before drawing a trading conclusion.",
    "counterargument": "Inventory buffers, allocation contracts and alternative production can offset a supplier change; the news may already be reflected in prices.",
    "falsifier": "The customer discloses that its relevant shipments are unaffected or are supplied by another foundry.",
}
STARTER_RELATIONSHIPS = [
    {
        **COMMON,
        "target_symbol": "AMD",
        "evidence_url": "https://www.sec.gov/Archives/edgar/data/2488/000000248826000018/amd-20251227.htm",
        "evidence_excerpt": "AMD's fiscal 2025 Form 10-K identifies TSMC as a wafer manufacturer for its high-performance computing, FPGA and adaptive SoC products.",
    },
    {
        **COMMON,
        "target_symbol": "NVDA",
        "evidence_url": "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm",
        "evidence_excerpt": "NVIDIA's fiscal 2026 Form 10-K lists TSMC and Samsung among the foundries it uses to manufacture semiconductor wafers.",
    },
]

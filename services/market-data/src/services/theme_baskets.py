"""The maintained theme baskets, and nothing else.

SPLIT OUT OF `theme_signals` (2026-10-12) because that module pulls an LLM client, Redis and
the ORM, so anything wanting only the membership list had to import all of it. Price-risk
detection wrapped that import in a try/except and silently fell back to NO BASKETS when it
failed — which disabled breadth detection entirely while looking exactly like "no theme fell".
A dependency-free module means the import either works or raises; there is nothing left to
swallow.

Hand-curated on purpose: `Stock.sector` is GICS-broad ("Semiconductors"), which cannot express
"AI / GPU" as distinct from "packaging and testing". See `theme_signals` for that history.
"""
from __future__ import annotations

THEMES: dict[str, list[str]] = {
    "AI / GPU Semiconductors": ["NVDA", "AMD", "AVGO", "TSM"],
    "Semiconductor Packaging & Testing": ["AMKR", "TSM", "MU"],
    "Passive Components (MLCC)": ["MU", "TXN", "ON"],
    "Gold & Precious Metals": ["GLD", "NEM", "GOLD", "AEM"],
    "Space & Satellite": ["RKLB", "LMT", "NOC", "ASTS"],
    "Healthcare & Biotech": ["UNH", "LLY", "JNJ", "ISRG"],
    "AI Infrastructure & Data Centers": ["MSFT", "GOOGL", "META", "AMZN"],
    "Clean Energy": ["ENPH", "FSLR", "TSLA", "NEE"],
}

def basket_for(theme: str) -> list[str]:
    return list(THEMES.get(theme, ()))

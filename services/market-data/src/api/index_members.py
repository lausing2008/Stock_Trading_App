"""Static index membership sets for US stock indices.

These sets are used to backfill the stocks.index_membership column.
Update periodically as index compositions change.
"""

DOW_30: set[str] = {
    "AAPL", "MSFT", "JPM", "V", "WMT", "UNH", "MCD", "AXP", "AMGN", "BA",
    "CAT", "CRM", "CVX", "DIS", "GS", "HD", "HON", "IBM", "INTC", "JNJ",
    "KO", "MRK", "MMM", "NKE", "PG", "TRV", "VZ", "DOW", "CSCO", "WBA",
}

NASDAQ_100: set[str] = {
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "TSLA", "GOOGL", "GOOG", "AVGO",
    "COST", "NFLX", "AMD", "ASML", "ADBE", "PEP", "CSCO", "TMUS", "QCOM",
    "TXN", "INTU", "AMGN", "SBUX", "ISRG", "BKNG", "REGN", "ADI", "VRTX",
    "PANW", "MU", "KLAC", "LRCX", "MDLZ", "SNPS", "CDNS", "AZN", "MELI",
    "FTNT", "CSGP", "ABNB", "KDP", "ORLY", "MNST", "TEAM", "DXCM", "BIIB",
    "CRWD", "WDAY", "IDXX", "ODFL", "ROST", "TTD", "ZS", "LCID", "FANG",
    "ANSS", "EXC", "DLTR", "FAST", "XEL", "KHC", "VRSK", "CTSH", "AEP",
    "PAYX", "MRNA", "ALGN", "CEG", "ON", "PCAR", "BKR", "EA", "GFS", "SMCI",
    "WBD", "CCEP", "GEHC", "CTAS", "HON", "CHTR", "CPRT", "CDW", "ACGL",
    "DDOG", "ROP", "MCHP", "ADSK", "PDD", "DASH", "GILD", "TTWO", "CSX",
    "LULU", "PYPL", "NXPI", "EBAY", "SGEN", "ILMN", "JD", "DOCU", "OKTA",
}

SP500: set[str] = {
    "AAPL", "MSFT", "AMZN", "NVDA", "GOOGL", "GOOG", "META", "TSLA",
    "BRK.B", "UNH", "JPM", "V", "XOM", "JNJ", "PG", "MA", "HD", "CVX",
    "MRK", "ABBV", "AVGO", "PEP", "KO", "LLY", "COST", "CSCO", "MCD",
    "WFC", "ADBE", "CRM", "ACN", "DIS", "NEE", "PFE", "BMY", "NFLX",
    "CMCSA", "TXN", "AMD", "VZ", "HON", "INTC", "QCOM", "UNP", "MS",
    "BAC", "GE", "RTX", "IBM", "LOW", "AMGN", "SBUX", "BLK", "INTU",
    "AXP", "SPGI", "GS", "CAT", "T", "LMT", "MDT", "DE", "AMAT", "MU",
    "NOW", "GILD", "PLD", "SYK", "CI", "CVS", "CB", "ISRG", "ADI",
    "EQIX", "ZTS", "BKNG", "REGN", "TJX", "HCA", "SO", "DUK", "APD",
    "ICE", "CL", "SCHW", "NSC", "FIS", "BSX", "ITW", "GD", "EMR", "SHW",
    "AON", "ATVI",
}

# Russell 2000 is too large to hardcode (2000 small-cap stocks).
# Populate via a data provider if needed.
RUSSELL_2000: set[str] = set()

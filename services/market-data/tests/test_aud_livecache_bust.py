"""AUD-LIVECACHE-BUST (2026-11-11): an ingest must not delete the live-price cache.

Deleting stockai:live_prices after every 5-minute ingest left every reader (price alerts, the
squeeze scans, the gamma scan) with nothing until the next minute's refresh. On Nov 11 the gamma
scan ran 16 s after each ingest finished and measured 0 symbols all day.
"""
import inspect

import src.services.ingestion as ING


def test_ingest_universe_does_not_delete_the_live_price_cache():
    src = inspect.getsource(ING.ingest_universe)
    assert ".delete(" not in src and "_bust_live_price_cache" not in src.split("# AUD-LIVECACHE-BUST")[0]
    assert not hasattr(ING, "_bust_live_price_cache")


def test_nothing_in_ingestion_deletes_the_cache():
    assert 'delete("stockai:live_prices")' not in inspect.getsource(ING)

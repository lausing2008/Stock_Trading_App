"""AUD-DP-AUDIENCE: owned_symbols_by_user on real models (SQLite). No provider calls."""
from pathlib import Path
p = Path(__file__).with_name('_price_risk_probe.py')
exec(compile(p.read_text().split('failures = []')[0], str(p), 'exec'))
db.PriceAlert = models.PriceAlert
models.Base.metadata.create_all(engine, tables=[models.PriceAlert.__table__])
import src.services.alert_audience as audience  # noqa: E402

with Session(engine) as s:
    for i, sym in enumerate(('AAPL', 'TSM', 'NVDA', 'MSFT'), 1):
        s.add(models.Stock(id=i, symbol=sym, name=sym, active=True, market=models.Market.US, exchange=models.Exchange.NASDAQ))
    for i in (1, 2, 3):
        s.add(models.User(id=i, username=f'u{i}', password_hash='x', is_active=True))
    s.flush()
    s.add(models.Watchlist(id=10, user_id=2, name='u2 list')); s.flush()
    s.add(models.WatchlistItem(id=1, user_id=1, stock_id=1))                     # direct owner: u1 AAPL
    s.add(models.WatchlistItem(id=2, user_id=None, watchlist_id=10, stock_id=2))  # via list owner: u2 TSM
    s.add(models.PriceAlert(id=1, user_id=1, symbol='nvda', condition=models.AlertCondition.ABOVE, threshold=1.0, triggered=False))
    s.add(models.PriceAlert(id=2, user_id=1, symbol='MSFT', condition=models.AlertCondition.ABOVE, threshold=1.0, triggered=True))
    s.commit()
    got = audience.owned_symbols_by_user(s, [1, 2, 3])
    assert got == {1: {'AAPL', 'NVDA'}, 2: {'TSM'}}, got     # triggered alert excluded; u3 owns nothing
    assert audience.owned_symbols_by_user(s, []) == {}
    assert audience.owned_symbols_by_user(s, [2]) == {2: {'TSM'}}

class Broken:
    def execute(self, *a, **k):
        raise RuntimeError('db down')
assert audience.owned_symbols_by_user(Broken(), [1]) == {}   # fails closed: nobody gets anything
print('OWNED_SYMBOLS_PROBE_OK')

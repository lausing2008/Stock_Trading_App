"""Explicit latest-provider captures, isolated from settled research/backtest archives.

One immutable capture feeds all requested expirations and calculations. Retrieval time is
never substituted for quote observation time. Redis failure/expiry never falls back to an archive.
"""
from __future__ import annotations

import base64
import json
import math
import re
import time
import uuid
import zlib
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .options_contract import OCC_SYMBOL, checked_contract_terms, supported_underlying

TTL = 600
PAGE_SIZE = 500
MAX_PAGES = 50
MAX_RAW = 12_000_000
MAX_COMPRESSED = 900_000
ET = ZoneInfo('America/New_York')
PACKED_FIELDS = ('option_symbol', 'expiry', 'right', 'strike', 'bid', 'ask', 'last_price',
    'last_price_available', 'volume', 'oi', 'iv', 'delta', 'gamma', 'theta', 'vega', 'rho', 'last_tape_time')


class SnapshotError(Exception):
    def __init__(self, code, message, retry_after=0):
        super().__init__(message)
        self.code, self.retry_after = code, retry_after


def symbol_key(symbol):
    sym = str(symbol).strip().upper()
    if not re.fullmatch(r'[A-Z][A-Z0-9.-]{0,9}', sym) or not supported_underlying(sym) or sym.endswith('.HK'):
        raise SnapshotError('unsupported_symbol', 'Current option snapshots support standard US equity/ETF symbols only.')
    return sym


def number(value):
    try:
        result = float(value)
        return result if not isinstance(value, bool) and math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def instant(value, now):
    try:
        stamp = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if stamp.tzinfo is None or stamp.utcoffset() is None or stamp > now + timedelta(seconds=30):
            return None
        return stamp.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError):
        return None


def normalize_row(raw, symbol, now):
    if not isinstance(raw, dict) or not isinstance(raw.get('option_symbol'), str):
        return None
    match = OCC_SYMBOL.fullmatch(raw['option_symbol'].strip().upper())
    if not match:
        return None
    try:
        expiry = datetime.strptime(match.group(2), '%y%m%d').date().isoformat()
    except ValueError:
        return None
    strike, right = int(match.group(4)) / 1000, 'call' if match.group(3) == 'C' else 'put'
    # The current-contract API encodes strike/expiry/side in its option_symbol. If an
    # extra explicit field is supplied, disagreement must not be silently discarded.
    terms = checked_contract_terms(raw['option_symbol'], symbol,
        strike=raw.get('strike', strike), expiry=raw.get('expiry', raw.get('expires', expiry)),
        right=raw.get('option_type', right))
    if not terms['automatic_payoff_supported']:
        return None
    def count(key):
        v = number(raw.get(key))
        return int(v) if v is not None and v >= 0 and v.is_integer() else None
    iv = number(raw.get('implied_volatility'))
    return {'option_symbol': raw['option_symbol'].strip().upper(), 'expiry': expiry, 'right': right,
        'strike': strike, 'bid': number(raw.get('nbbo_bid')) or 0.0,
        'ask': number(raw.get('nbbo_ask')) or 0.0, 'last_price': number(raw.get('last_price')) or 0.0,
        'last_price_available': number(raw.get('last_price')) is not None,
        'volume': count('volume'), 'oi': count('open_interest'),
        'iv': iv * 100 if iv is not None and iv >= 0 else None,
        **{key: number(raw.get(key)) for key in ('delta', 'gamma', 'theta', 'vega', 'rho')},
        'last_tape_time': instant(raw.get('last_tape_time'), now),
        'quote_observed_at': None, 'itm': None, 'moneyness': None, 'contract_terms': terms}


def capture(symbol, rdb, fetch, *, expiry=None, clock=lambda: datetime.now(timezone.utc), timer=time.monotonic):
    sym = symbol_key(symbol)
    started, started_mono = clock(), timer()
    if expiry is not None:
        try:
            valid_expiry = date.fromisoformat(expiry)
            if valid_expiry.isoformat() != expiry or valid_expiry < started.astimezone(ET).date():
                raise ValueError()
        except (ValueError, TypeError):
            raise SnapshotError('invalid_expiry', 'Select a current expiration in YYYY-MM-DD form before refreshing.')
        from .options_scope import current_expiries
        if not current_expiries([expiry], started.astimezone(ET).date(), started, symbol=sym):
            raise SnapshotError('expiry_closed', 'That expiration has stopped trading. Select a current expiration.')
    token = uuid.uuid4().hex
    lock = f'options_snapshot_capture:v1:{sym}'
    try:
        if not rdb.set(lock, token, nx=True, ex=180):
            raise SnapshotError('busy', 'Another capture or refresh cooldown is active. Retry shortly.', max(1, rdb.ttl(lock)))
    except SnapshotError:
        raise
    except Exception as exc:
        raise SnapshotError('cache_unavailable', 'Snapshot storage is unavailable; no provider capture was started.') from exc
    try:
        rows, seen, rejected, pages = [], set(), 0, 0
        terminal = False
        for page in range(MAX_PAGES):
            if timer() - started_mono > 45:
                raise SnapshotError('capture_timeout', 'The full chain did not finish within the capture budget; previous data is retained.')
            batch = fetch(f'/api/stock/{sym}/option-contracts', params={
                'page': page, 'limit': PAGE_SIZE, 'exclude_zero_vol_chains': 'false',
                'exclude_zero_oi_chains': 'false', 'exclude_zero_dte': 'false',
                'maybe_otm_only': 'false', 'vol_greater_oi': 'false', **({'expiry': expiry} if expiry else {})},
                endpoint='/api/stock/{ticker}/option-contracts')
            pages += 1
            if not isinstance(batch, list):
                raise SnapshotError('provider_data_failure', 'The current-contract feed did not return a readable chain.')
            for raw in batch:
                key = raw.get('option_symbol') if isinstance(raw, dict) else None
                if key and key in seen:
                    raise SnapshotError('pagination_changed', 'The provider repeated a contract during pagination; a complete capture cannot be established. Retry.')
                if key:
                    seen.add(key)
                row = normalize_row(raw, sym, clock())
                if row is None:
                    rejected += 1
                else:
                    if expiry and row['expiry'] != expiry:
                        raise SnapshotError('provider_scope_mismatch', 'The provider returned another expiration; no mixed-scope snapshot was published.')
                    rows.append(row)
            if len(batch) < PAGE_SIZE:
                terminal = True
                break
        if not terminal:
            raise SnapshotError('page_limit', 'The chain exceeds the capture page limit. Select one expiration in the refresh control; no partial snapshot was published.')
        if not rows:
            raise SnapshotError('no_usable_contracts', 'The current feed returned no supported readable contracts.')
        raw_spot = fetch(f'/api/stock/{sym}/stock-state', endpoint='/api/stock/{ticker}/stock-state')
        spot = number(raw_spot.get('close')) if isinstance(raw_spot, dict) else None
        spot_time = instant(raw_spot.get('tape_time'), clock()) if isinstance(raw_spot, dict) else None
        if spot is None or spot <= 0 or spot_time is None:
            raise SnapshotError('stock_reference_unavailable', 'A valid timed stock reference is required; no archive price was substituted.')
        completed = clock()
        # Last-trade session is a proxy for the data date, NOT a verified NBBO timestamp.
        times = [r['last_tape_time'] for r in rows if r['last_tape_time']] + [spot_time]
        source_session = max(datetime.fromisoformat(t).astimezone(ET).date() for t in times).isoformat()
        from .options_scope import current_expiries
        eligible = current_expiries(sorted({r['expiry'] for r in rows}), date.fromisoformat(source_session), completed, symbol=sym)
        metadata = {'id': token, 'symbol': sym, 'source': 'unusual_whales_current_contracts',
            'capture_started_at': started.isoformat(), 'captured_at': completed.isoformat(),
            'expires_at': (completed + timedelta(seconds=TTL)).isoformat(),
            'refresh_after': (completed + timedelta(seconds=60)).isoformat(),
            'stock_reference': spot, 'stock_observed_at': spot_time,
            'stock_market_time': raw_spot.get('market_time'), 'source_session': source_session,
            'source_session_basis': 'latest reported trade date; not NBBO observation time',
            'capture_expiry': expiry,
            'quote_observation_time_available': False, 'pages': pages, 'contracts': len(rows),
            'rejected_contracts': rejected, 'pagination_complete': True,
            'expiries': sorted({r['expiry'] for r in rows}), 'current_expiries': eligible,
            'limitations': 'Latest available provider bid/ask and Greeks captured over a window, not simultaneous broker quotes. Trade times are not quote times; OI can update daily. Entry still requires fresh broker checks.'}
        # Columnar encoding avoids repeating field names and identical assumption text thousands
        # of times. Terms are checked again on load; quote/moneyness unknowns stay unknown.
        payload = {'metadata': metadata, 'columns': list(PACKED_FIELDS),
                   'values': [[row[field] for field in PACKED_FIELDS] for row in rows]}
        raw = json.dumps(payload, allow_nan=False, separators=(',', ':')).encode()
        packed = zlib.compress(raw)
        if len(raw) > MAX_RAW or len(packed) > MAX_COMPRESSED:
            raise SnapshotError('snapshot_too_large', 'The complete snapshot exceeds storage limits. Select one expiration in the refresh control; no partial data was published.')
        rdb.setex(f'options_snapshot:v1:{sym}:{token}', TTL, base64.b64encode(packed).decode())
        rdb.eval("if redis.call('get',KEYS[1]) == ARGV[1] then return redis.call('expire',KEYS[1],60) else return 0 end", 1, lock, token)
        return metadata
    except Exception as exc:
        try:
            cooldown = 60 if type(exc).__name__ == 'UnusualWhalesRateLimitError' else 10
            rdb.eval(f"if redis.call('get',KEYS[1]) == ARGV[1] then return redis.call('expire',KEYS[1],{cooldown}) else return 0 end", 1, lock, token)
        except Exception:
            pass
        if isinstance(exc, SnapshotError):
            raise
        code = 'rate_limited' if type(exc).__name__ == 'UnusualWhalesRateLimitError' else 'capture_failed'
        raise SnapshotError(code, 'Latest-chain capture failed; previous data is retained. No archive fallback was presented as refreshed data.', 60 if code == 'rate_limited' else 10) from exc


def load(symbol, snapshot_id, rdb, *, now=None):
    sym = symbol_key(symbol)
    if not re.fullmatch(r'[0-9a-f]{32}', str(snapshot_id)):
        raise SnapshotError('invalid_snapshot', 'Invalid snapshot identifier. Refresh the option chain.')
    try:
        encoded = rdb.get(f'options_snapshot:v1:{sym}:{snapshot_id}')
        if not encoded:
            raise SnapshotError('snapshot_expired', 'Snapshot expired or was evicted. Refresh the option chain; archive fallback is disabled.')
        packed = base64.b64decode(encoded, validate=True)
        if len(packed) > MAX_COMPRESSED:
            raise ValueError('oversized capture')
        decoder = zlib.decompressobj()
        raw = decoder.decompress(packed, MAX_RAW + 1)
        if len(raw) > MAX_RAW or decoder.unconsumed_tail or not decoder.eof:
            raise ValueError('oversized or truncated capture')
        data = json.loads(raw)
        meta = data['metadata']
        now = now or datetime.now(timezone.utc)
        if meta['id'] != snapshot_id or meta['symbol'] != sym or datetime.fromisoformat(meta['expires_at']) <= now:
            raise SnapshotError('snapshot_expired', 'Snapshot expired or does not match this stock. Refresh the option chain.')
        if data['columns'] != list(PACKED_FIELDS) or not isinstance(data['values'], list) or len(data['values']) > MAX_PAGES * PAGE_SIZE:
            raise ValueError('invalid capture schema')
        rows = []
        for values in data['values']:
            if not isinstance(values, list) or len(values) != len(PACKED_FIELDS):
                raise ValueError('invalid capture row')
            row = dict(zip(PACKED_FIELDS, values))
            terms = checked_contract_terms(row['option_symbol'], sym, strike=row['strike'], expiry=row['expiry'], right=row['right'])
            if not terms['automatic_payoff_supported']:
                raise ValueError('corrupt contract identity')
            rows.append({**row, 'contract_terms': terms, 'quote_observed_at': None, 'itm': None, 'moneyness': None})
        return {'metadata': meta, 'rows': rows}
    except SnapshotError:
        raise
    except Exception as exc:
        raise SnapshotError('snapshot_unavailable', 'Snapshot could not be read. Refresh the option chain; no archive fallback was used.') from exc


def chain_view(data, expiry=None, *, now=None):
    from .options_scope import expiry_scope
    meta = data['metadata']
    scope = expiry_scope(meta['expiries'], date.fromisoformat(meta['source_session']), now, symbol=meta['symbol'])
    chosen = expiry or next(iter(scope['current_expiries']), None)
    if chosen not in meta['expiries']:
        return {'symbol': meta['symbol'], 'available': False, 'reason': 'expiry_not_in_snapshot',
                'expiries': meta['expiries'], 'current_expiries': scope['current_expiries'], 'option_snapshot': meta}
    rows = [r for r in data['rows'] if r['expiry'] == chosen]
    return {'symbol': meta['symbol'], 'available': True, 'expiry': chosen,
        'expiries': meta['expiries'], 'current_expiries': scope['current_expiries'],
        'expiry_scope': scope, 'expiry_is_current': chosen in scope['current_expiries'],
        'source_session': meta['source_session'], 'option_snapshot': meta,
        'calls': sorted((r for r in rows if r['right'] == 'call'), key=lambda r: r['strike']),
        'puts': sorted((r for r in rows if r['right'] == 'put'), key=lambda r: r['strike']),
        'coverage_status': 'provider_pages_complete', 'coverage_reason': 'terminal_page',
        'moneyness_reference': None, 'moneyness_unknown_reason': 'Snapshot quote/reference observation basis is not verified.'}


def calculate_plan(data, compute_plan, choose_expiry, *, today, stop_loss=None, take_profit=None,
                   shares=None, holding_days=None, preferences=None, now=None):
    from .options_scope import expiry_scope
    from .options_strategies import _covered_call_candidates, covered_call_expiry_search
    meta = data['metadata']
    scope = expiry_scope(meta['expiries'], date.fromisoformat(meta['source_session']), now, symbol=meta['symbol'])
    expiries = scope['current_expiries']
    put_exp, call_exp = choose_expiry(expiries, today, 25, 60), choose_expiry(expiries, today, 14, 45)
    def rows(expiry, right):
        return [r for r in data['rows'] if r['expiry'] == expiry and r['right'] == right]
    puts, calls = rows(put_exp, 'put'), rows(call_exp, 'call')
    spot = meta['stock_reference']
    plan = compute_plan(current_price=spot, stop_loss=stop_loss, take_profit=take_profit,
        signal=None, put_expiries=[put_exp] if put_exp else [], put_rows=puts,
        call_expiries=[call_exp] if call_exp else [], call_rows=calls, shares=shares, today=today)
    matrix = plan.get('strategy_matrix') or {}
    if holding_days is not None:
        specs = {'covered_call': (calls, call_exp, spot, 'call'), 'long_call': (calls, call_exp, spot, 'call'),
                 'protective_put': (puts, put_exp, stop_loss or spot, 'put'),
                 'cash_secured_put': (puts, put_exp, stop_loss or spot, 'put')}
        for key, (source, expiry, anchor, right) in specs.items():
            original = matrix.get('candidate_review', {}).get(key)
            if original is None:
                original = _covered_call_candidates(source, anchor, expiry,
                    (date.fromisoformat(expiry)-today).days if expiry else None,
                    today, spot, strategy_key=key)
            matrix.setdefault('candidate_review', {})[key] = covered_call_expiry_search(
                original=original, expiries=expiries, today=today, holding_days=holding_days,
                current_price=spot, strategy_key=key, preferences=preferences,
                read_rows=lambda e, right=right: rows(e, right))
    for review in matrix.get('candidate_review', {}).values():
        review['listing_verification'] = 'provider_current_contracts'
        review['snapshot_id'] = meta['id']
    plan.update(symbol=meta['symbol'], chain_as_of=meta['source_session'],
                chain_source=meta['source'], option_snapshot=meta,
                available=bool(plan.get('covered_call') or plan.get('protective_put') or matrix.get('singles')
                    or any(r.get('alternatives') for r in matrix.get('candidate_review', {}).values())),
                reason=None if expiries else 'no_current_expiries')
    return plan

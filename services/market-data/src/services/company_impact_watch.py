"""General company-development coverage from existing stored headlines; SHADOW ONLY.

Provider tags and headline wording are observations, not independent confirmation or forecasts.
No model call, mail provider, in-app notification or outbox enqueue is reachable here.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import re
from urllib.parse import urlsplit
from sqlalchemy import select
from db import NewsImpactEvent, NewsImpactReceipt, RealtimeNewsItem, Stock, WatchlistItem, Watchlist, User

CATEGORIES = (
    ('guidance', r'\b(?:guidance|outlook|forecast)\b.*\b(?:rais\w*|cut\w*|lower\w*|boost\w*|updat\w*|revis\w*)\b|\b(?:rais\w*|cut\w*|lower\w*)\b.*\b(?:guidance|outlook|forecast)\b'),
    ('regulatory', r'\b(?:antitrust|recall|lawsuit|regulator\w*|investigation|FDA|approval|sanctions?)\b'),
    ('contract', r'\b(?:wins?|awarded|secures?|signs?)\b.*\b(?:contract|order|deal)\b'),
    ('partnership', r'\b(?:partner\w*|collaborat\w*|strategic alliance|joint venture)\b'),
    ('product', r'\b(?:launch\w*|unveils?|introduces?|pre-?orders?|releases?)\b.*\b(?:chip|processor|device|product|platform|model|software|service|laptop|GPU|CPU)\b'),
    ('leadership', r'\b(?:CEO|CFO|chief executive|chief financial)\b.*\b(?:resigns?|steps down|appointed|names?|retires?)\b|\b(?:appoints?|names?)\b.*\b(?:CEO|CFO)\b'),
    ('earnings', r'\b(?:reports?|announces?|posts?)\b.*\b(?:earnings|quarterly results|financial results)\b'),
)


def category(headline):
    return next((name for name, pattern in CATEGORIES if re.search(pattern, headline, re.I)), None)


def aliases(stock):
    name = re.sub(r'\b(?:incorporated|inc|corporation|corp|limited|ltd|plc|class [ab])\b[.,]?', '', stock.name or '', flags=re.I).strip(' ,.')
    patterns = []
    if len(name) >= 4:
        patterns.append(re.compile(r'\b'+re.escape(name)+r'\b', re.I))
    if len(stock.symbol) >= 3:
        patterns.append(re.compile(r'(?<!\w)\$?'+re.escape(stock.symbol)+r'(?!\w)'))
    return patterns


def impacts_for(row, stocks, kind, patterns=None):
    text = row.headline or ''
    impacts = []
    for stock in stocks:
        found = next((m for p in (patterns[stock.symbol] if patterns is not None else aliases(stock)) if (m := p.search(text))), None)
        if stock.symbol != row.symbol and found is None:
            continue
        role, direction = ('provider_tagged_company' if stock.symbol == row.symbol else 'mentioned_company'), 'unmeasured'
        quote = None
        if found:
            before = text[max(0, found.start()-55):found.start()]
            uncertain = re.search(r'\b(?:not|never|no longer|may|might|could|rumou?red|denies?)\b', before, re.I)
            if uncertain:
                # Keep the company association without turning negation/speculation into a role.
                pass
            elif re.search(r'\b(?:replaces?|drops?|switch(?:es)? away from)\s*$', before, re.I):
                role, direction = 'reported_displaced_supplier', 'negative_watch'
                quote = before + found.group(0)
            elif re.search(r'\b(?:powered by|processors? from|chips? from|supplied by)\s*$', before, re.I):
                role, direction = 'reported_supplier', 'positive_watch'
                quote = before + found.group(0)
            elif re.search(r'\b(?:partnership with|partners? with|collaborates? with)\s*$', before, re.I):
                role = 'reported_partner'; quote = before + found.group(0)
        impacts.append({'symbol': stock.symbol, 'direction': direction, 'role': role,
            'category': kind, 'source_excerpt': quote,
            'reason': f'{kind.title()} headline associated with {stock.symbol}; role is reported, not independently verified. Revenue and share-price effects are unmeasured.'})
    return impacts


def process_recent(session, *, now=None):
    now = now or datetime.now(timezone.utc)
    naive = now.astimezone(timezone.utc).replace(tzinfo=None) if now.tzinfo else now
    stocks = list(session.scalars(select(Stock).where(Stock.active.is_(True))))
    known = {s.symbol for s in stocks}
    patterns = {s.symbol: aliases(s) for s in stocks}
    rows = list(session.scalars(select(RealtimeNewsItem).where(
        RealtimeNewsItem.published_at >= naive-timedelta(days=2),
        RealtimeNewsItem.published_at <= naive).order_by(RealtimeNewsItem.published_at.desc()).limit(2000)))
    watchers = {}
    for direct, owner, symbol in session.execute(select(WatchlistItem.user_id, Watchlist.user_id, Stock.symbol)
            .join(Stock, Stock.id == WatchlistItem.stock_id)
            .outerjoin(Watchlist, Watchlist.id == WatchlistItem.watchlist_id)):
        uid = direct or owner
        if uid is not None: watchers.setdefault(uid, set()).add(symbol)
    active_users = set(session.scalars(select(User.id).where(User.is_active.is_(True))))
    stats = dict(scanned=len(rows), classified=0, events=0, receipts=0, unbound=0, unsafe_url=0, sent=0)
    for row in rows:
        kind = category(row.headline or '')
        if kind is None: continue
        stats['classified'] += 1
        if row.symbol not in known:
            stats['unbound'] += 1; continue
        try:
            parsed = urlsplit(row.url or '')
            safe_url = parsed.scheme in ('http', 'https') and bool(parsed.hostname) and not parsed.username
        except ValueError:
            safe_url = False
        if not safe_url:
            stats['unsafe_url'] += 1; continue
        normalized = ' '.join((row.headline or '').casefold().split())
        key = 'company:' + hashlib.sha256((normalized+'|'+str(row.published_at.date())).encode()).hexdigest()
        event = session.scalar(select(NewsImpactEvent).where(NewsImpactEvent.event_key == key))
        if event is None:
            impacts = impacts_for(row, stocks, kind, patterns)
            event = NewsImpactEvent(event_key=key, title=(row.headline or '')[:512], source_url=row.url,
                observed_url=row.url, published_at=row.published_at, impacts=impacts,
                evidence_status='provider_headline_unverified')
            session.add(event); session.flush(); stats['events'] += 1
        # Duplicate provider rows can associate another explicitly tagged issuer with this event.
        if row.symbol not in {x['symbol'] for x in event.impacts}:
            event.impacts = list(event.impacts) + [{'symbol': row.symbol, 'direction': 'unmeasured',
                'role': 'provider_tagged_company', 'category': kind,
                'reason': 'Provider association only; financial and stock effects unmeasured.'}]
        affected = {x['symbol'] for x in event.impacts}
        for uid, followed in watchers.items():
            matched = affected & followed
            if uid not in active_users or not matched: continue
            if session.scalar(select(NewsImpactReceipt.id).where(NewsImpactReceipt.event_id == event.id, NewsImpactReceipt.user_id == uid)):
                continue
            historical = naive-row.published_at > timedelta(hours=4)
            session.add(NewsImpactReceipt(event_id=event.id, user_id=uid, matched_symbols=sorted(matched),
                state='historical_no_alert' if historical else 'shadow_suppressed',
                reason='Company-event coverage is shadow only; provider headline is unverified.'))
            session.flush(); stats['receipts'] += 1
    return stats

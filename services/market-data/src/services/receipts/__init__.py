"""Durable measurement records (2026-10-30): price-risk scan records and paper entry receipts.

Both answer questions a log line could not: the price-risk acceptance trial needs every scan's
coverage, exclusions, suppressions and configuration, and the fresh-mark decision needs every
entry attempt with the prices and timestamps it was decided on. Logs were lost on each container
recreate, so neither question could be answered for October (see
docs/2026-10-29/deferred-checkpoints-review.md).

Market-data's own MetaData, like the Intraday tables: nothing else creates these, and adding them
to shared/db would force a rebuild of all twelve backends.
"""

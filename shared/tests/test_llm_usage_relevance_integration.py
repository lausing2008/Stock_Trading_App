"""Does the relevance query run against a REAL `json` column — the type production actually has?

AUD-LLMUSAGE-JSONCAST. The original defect was invisible to every unit test: `llm_call_log.context`
is declared `json`, the key-exists operator `?` and `jsonb_array_length` exist only for `jsonb`,
and PostgreSQL raises UndefinedFunction at execution time. A fake session, a SQLite session and a
source-text assertion all accept the broken SQL happily. Only a real PostgreSQL table whose column
is really `json` can reject it.

So these tests create the column with the production type and execute the production SQL.

Gated on BUDGET_PG_URL, the same harness `make test-integration` already spins up.
"""
import os

import pytest
from sqlalchemy import create_engine, text

PG_URL = os.environ.get("BUDGET_PG_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="BUDGET_PG_URL not set")

# The two statements exactly as admin.py issues them. Kept here as literals on purpose: a test
# that imported them could not detect the SQL being changed to something unrunnable.
SCOPE_SQL = """
    SELECT COALESCE((context::jsonb->'scope_counts'->>'tracked')::int, 0)        AS tracked,
           COALESCE((context::jsonb->'scope_counts'->>'market_context')::int, 0) AS ctx,
           COALESCE((context::jsonb->'scope_counts'->>'out_of_scope')::int, 0)   AS oos,
           COALESCE(input_tokens, 0) + COALESCE(output_tokens, 0)                AS tokens
    FROM llm_call_log
    WHERE created_at >= :since
      AND context IS NOT NULL
      AND context::jsonb ? 'scope_counts'
"""
DIGEST_SQL = """
    SELECT jsonb_array_elements_text(context::jsonb->'article_digests') AS d
    FROM llm_call_log
    WHERE created_at >= :since
      AND context IS NOT NULL
      AND context::jsonb ? 'article_digests'
"""


@pytest.fixture()
def engine():
    e = create_engine(PG_URL, future=True)
    with e.begin() as c:
        c.execute(text("DROP TABLE IF EXISTS llm_call_log"))
        # `json`, NOT `jsonb`. This single word is the whole point of the fixture.
        c.execute(text("""
            CREATE TABLE llm_call_log (
                id serial PRIMARY KEY,
                created_at timestamptz NOT NULL DEFAULT now(),
                input_tokens int, output_tokens int,
                context json
            )"""))
    yield e
    with e.begin() as c:
        c.execute(text("DROP TABLE IF EXISTS llm_call_log"))
    e.dispose()


def _insert(engine, context_json: str | None, tokens=(10, 5)):
    with engine.begin() as c:
        c.execute(text("INSERT INTO llm_call_log (input_tokens, output_tokens, context) "
                       "VALUES (:i, :o, CAST(:ctx AS json))"),
                  {"i": tokens[0], "o": tokens[1], "ctx": context_json})


def test_the_column_really_is_json_not_jsonb(engine):
    """If this fixture ever drifts to jsonb, every test below passes for the wrong reason."""
    with engine.connect() as c:
        t = c.execute(text("SELECT data_type FROM information_schema.columns "
                           "WHERE table_name='llm_call_log' AND column_name='context'")).scalar()
    assert t == "json", f"fixture must reproduce production's type, got {t!r}"


def test_the_scope_query_executes_against_a_json_column(engine):
    _insert(engine, '{"scope_counts": {"tracked": 3, "market_context": 1, "out_of_scope": 7}}')
    with engine.connect() as c:
        rows = c.execute(text(SCOPE_SQL), {"since": "2000-01-01"}).all()
    assert [tuple(r) for r in rows] == [(3, 1, 7, 15)]


def test_the_digest_query_executes_against_a_json_column(engine):
    _insert(engine, '{"article_digests": ["a", "b", "a"]}')
    with engine.connect() as c:
        rows = c.execute(text(DIGEST_SQL), {"since": "2000-01-01"}).all()
    digests = [r[0] for r in rows]
    assert digests == ["a", "b", "a"]
    assert len(digests) - len(set(digests)) == 1, "the repeat must be countable"


def test_the_uncast_query_really_does_fail_on_this_column(engine):
    """The defect reproduced. Without this, a cast could be dropped and nothing would notice."""
    _insert(engine, '{"scope_counts": {"tracked": 1}}')
    with engine.connect() as c:
        with pytest.raises(Exception) as exc:
            c.execute(text("SELECT 1 FROM llm_call_log WHERE context ? 'scope_counts'")).all()
    assert "operator does not exist" in str(exc.value).lower()


def test_a_null_context_is_skipped_rather_than_raising(engine):
    _insert(engine, None)
    _insert(engine, '{"scope_counts": {"tracked": 2}}')
    with engine.connect() as c:
        rows = c.execute(text(SCOPE_SQL), {"since": "2000-01-01"}).all()
    assert [r[0] for r in rows] == [2]


def test_a_row_without_the_key_is_excluded_not_counted_as_zero(engine):
    """Excluding differs from counting zero: one reduces the denominator, the other does not."""
    _insert(engine, '{"something_else": 1}')
    _insert(engine, '{"scope_counts": {"tracked": 4}}')
    with engine.connect() as c:
        rows = c.execute(text(SCOPE_SQL), {"since": "2000-01-01"}).all()
    assert len(rows) == 1 and rows[0][0] == 4


def test_a_json_scalar_in_context_does_not_take_the_endpoint_down(engine):
    """A non-object context is malformed data, and the query must tolerate it, not 500."""
    _insert(engine, '"a bare string"')
    _insert(engine, '{"scope_counts": {"tracked": 5}}')
    with engine.connect() as c:
        rows = c.execute(text(SCOPE_SQL), {"since": "2000-01-01"}).all()
    assert [r[0] for r in rows] == [5]

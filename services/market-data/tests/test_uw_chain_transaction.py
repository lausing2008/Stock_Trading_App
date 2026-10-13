"""The options-chain fetch must not hold a database transaction across a provider call.

Production showed connections `idle in transaction` for 30-35 seconds per symbol while the
process waited on Unusual Whales — holding locks and an MVCC snapshot on somebody else's
network latency, the same shape as the 2026-10-12 migration convoy's orphan. Run outside the
conftest stubs so `session.in_transaction()` is a real answer from a real session.
"""
import subprocess
import sys
from pathlib import Path


def test_no_transaction_is_held_across_the_provider_call():
    probe = Path(__file__).with_name('_uw_chain_txn_probe.py')
    result = subprocess.run([sys.executable, str(probe)], capture_output=True, text=True,
                            timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr

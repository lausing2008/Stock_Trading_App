"""Which code produced a record: the deployed commit, and a hash of the source that ran.

Two fields because neither alone is enough. `release_sha` is what the deploy says it built
(`STOCKAI_RELEASE_SHA`, set by scripts/deploy_remote.sh). A container recreated by hand, or one
patched with `docker cp`, still carries whatever that variable was, or nothing. The fingerprint is
computed from the module files actually loaded, so it changes with the code even when nobody
updated the variable. A missing variable is recorded as "unrecorded", never guessed.
"""
from __future__ import annotations

import hashlib
import os

RELEASE_ENV = "STOCKAI_RELEASE_SHA"
UNRECORDED = "unrecorded"

_fingerprints: dict[tuple[str, ...], str] = {}


def release_sha() -> str:
    value = (os.environ.get(RELEASE_ENV) or "").strip()
    return value[:64] if value else UNRECORDED


def code_fingerprint(modules) -> str:
    """sha256 over each module's name and source bytes, in name order. Cached per process: the
    source cannot change without a restart, which re-imports it."""
    named = sorted(((m.__name__, getattr(m, "__file__", None)) for m in modules), key=lambda x: x[0])
    key = tuple(n for n, _ in named)
    if key not in _fingerprints:
        h = hashlib.sha256()
        for name, path in named:
            h.update(name.encode() + b"\0")
            try:
                with open(path, "rb") as fh:
                    h.update(fh.read())
            except (OSError, TypeError):
                h.update(b"<source unavailable>")
            h.update(b"\0")
        _fingerprints[key] = h.hexdigest()
    return _fingerprints[key]


def identity(modules) -> dict:
    return {"release_sha": release_sha(), "code_fingerprint": code_fingerprint(modules),
            "fingerprint_modules": sorted(m.__name__ for m in modules)}

"""M22 — the OpenRouter credential is a WORKER-ONLY secret, and must stay that way.

The Jev design is explicit: "Use a worker-only `OPENROUTER_API_KEY` secret. Do not put keys in
browser storage, public feature flags, task payloads or logs."

The shared `.env` is loaded by all twelve services through `env_file: ../.env`, so putting it
there would hand a text-classification credential to the trading engine, the gateway and the
ranking service — none of which has any use for it. It lives in `.env.jev`, which only
news-intelligence loads.
"""
import pathlib

import yaml

_ROOT = pathlib.Path(__file__).resolve().parents[3]
_COMPOSE = yaml.safe_load((_ROOT / "docker" / "docker-compose.yml").read_text())


def _env_files(service: str) -> list:
    raw = _COMPOSE["services"][service].get("env_file")
    if raw is None:
        return []
    return raw if isinstance(raw, list) else [raw]


def _paths(service: str) -> list[str]:
    return [e if isinstance(e, str) else e.get("path") for e in _env_files(service)]


def test_only_news_intelligence_loads_the_jev_env_file():
    """One service, not twelve. A credential reaching services that cannot use it is a larger
    blast radius for no benefit."""
    holders = [name for name in _COMPOSE["services"] if "../.env.jev" in _paths(name)]
    assert holders == ["news-intelligence"], holders


def test_the_stack_still_starts_without_the_file():
    """Absent is the NORMAL state — Jev is off by default. A required file would make every
    deploy depend on a credential nobody has configured yet."""
    entry = next(e for e in _env_files("news-intelligence")
                 if not isinstance(e, str) and e.get("path") == "../.env.jev")
    assert entry.get("required") is False


def test_the_key_is_not_in_the_shared_env_example():
    """The shared example is what a new deployment copies. Listing the key there would put it
    back in every service by default — the exact thing this scoping prevents."""
    for candidate in (".env.example", ".env.production.example"):
        f = _ROOT / candidate
        if f.exists():
            assert "OPENROUTER_API_KEY" not in f.read_text(), candidate


def test_the_example_file_is_actually_COMMITTED_not_just_present_on_disk():
    """It was not, and the failure was silent. `.gitignore`'s `.env.*` rule matches
    `.env.jev.example` too, and the existing negations only cover the two older examples — so
    `git add -A` skipped it without a word, the commit succeeded, and the file simply was not
    on the server when the deploy tried to copy it.

    An example that exists only on the author's machine documents nothing."""
    import subprocess
    r = subprocess.run(["git", "check-ignore", ".env.jev.example"],
                       cwd=_ROOT, capture_output=True, text=True)
    assert r.returncode != 0, ".env.jev.example is gitignored and will never reach a deployment"


def test_the_example_file_is_present_and_carries_no_value():
    """A committed example must document the name and never a secret."""
    example = (_ROOT / ".env.jev.example").read_text()
    assert "OPENROUTER_API_KEY=" in example
    assert example.strip().endswith("OPENROUTER_API_KEY="), "the example must have NO value"


def test_a_configured_key_does_not_by_itself_enable_jev():
    """Two independent controls. The credential makes a request POSSIBLE; `jev_enabled` decides
    whether one is made, and it defaults off — so a key present with the flag off is inert."""
    admin = (_ROOT / "services" / "market-data" / "src" / "api" / "admin.py").read_text()
    assert '_REDIS_JEV_ENABLED' in admin
    assert 'r.get(_REDIS_JEV_ENABLED) == "1"' in admin, \
        "absence of the flag must read as OFF, not as enabled"

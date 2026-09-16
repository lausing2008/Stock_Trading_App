"""AUD-GATEWAY-ROUTECOVERAGE: every backend router declared with APIRouter(prefix="/x") and
actually registered in that service's main.py must have a matching entry in proxy.py's
_ROUTES table, or every request under that prefix 404s at the gateway despite the backend
fully implementing the feature.

This is not a hypothetical: it has now happened 3 times for real, each caught live rather than
in review — rl-agent (RL Agent tile 404'd), conditional-orders (T286 shipped but every request
404'd), and options-income (T398's Create Portfolio modal returned a raw "404 Not Found" the
first time it was deployed). Each fix left a comment in proxy.py's _ROUTES table describing
the exact same gap. Three independent occurrences of one bug class is worth a structural test
rather than a fourth silent recurrence.

Pure source-text scanning (matches this codebase's own established convention — e.g.
test_alerts_env_gate.py's scheduler-job classification, navGuardParity.test.ts's nav-vs-page
guard check — for exactly this class of "read the real registration wiring, don't just assume
it's correct" test), not an import: these services have heavy, container-only dependency
chains that don't resolve in a local test environment.

Three services (decision-engine, event-intelligence, strategy-engine) declare their router as
bare `APIRouter()` with no prefix — every route there carries its own full path instead, and
the gateway maps to them by a different mechanism. That's a legitimately different, deliberate
pattern, not an instance of this bug class, so a router with no `prefix=` argument is correctly
skipped rather than flagged.
"""
import re
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
SERVICES_DIR = REPO_ROOT / "services"
PROXY_SRC = (REPO_ROOT / "services/api-gateway/src/api/proxy.py").read_text()


def _services_to_check() -> list[pathlib.Path]:
    return sorted(
        d for d in SERVICES_DIR.iterdir()
        if d.is_dir() and d.name != "api-gateway" and (d / "src" / "main.py").exists()
    )


def _extract_router_prefixes(service_dir: pathlib.Path) -> set[str]:
    """Every prefix from an APIRouter(prefix="/x") whose variable is actually included in
    this service's main.py `routers=[...]` list — a router file that exists but is never
    wired into main.py is correctly ignored (dead code, not a live 404 risk)."""
    main_src = (service_dir / "src" / "main.py").read_text()
    m = re.search(r"routers\s*=\s*\[([^\]]*)\]", main_src)
    if not m:
        return set()
    router_names = [n.strip() for n in m.group(1).split(",") if n.strip()]

    import_map: dict[str, tuple[str, str]] = {}
    for imp_m in re.finditer(r"from \.api\.(\w+) import ([^\n]+)", main_src):
        module = imp_m.group(1)
        for piece in imp_m.group(2).split(","):
            piece = piece.strip()
            if " as " in piece:
                orig, alias = piece.split(" as ")
                import_map[alias.strip()] = (module, orig.strip())
            else:
                import_map[piece] = (module, piece)

    prefixes: set[str] = set()
    for rname in router_names:
        if rname not in import_map:
            continue
        module, orig_var = import_map[rname]
        module_path = service_dir / "src" / "api" / f"{module}.py"
        if not module_path.exists():
            continue
        module_src = module_path.read_text()
        pm = re.search(
            rf"{re.escape(orig_var)}\s*=\s*APIRouter\([^)]*prefix\s*=\s*[\"']([^\"']+)[\"']",
            module_src,
        )
        if pm:
            prefixes.add(pm.group(1).lstrip("/"))
    return prefixes


def _gateway_routes() -> set[str]:
    m = re.search(r"_ROUTES\s*=\s*\{(.*?)\n\}", PROXY_SRC, re.DOTALL)
    assert m, "could not find _ROUTES dict in proxy.py — parser or file moved?"
    return set(re.findall(r'"([a-zA-Z0-9_-]+)"\s*:', m.group(1)))


GATEWAY_ROUTES = _gateway_routes()
SERVICES = _services_to_check()


def test_found_a_realistic_number_of_gateway_routes():
    # Guards against a regex that silently matches nothing, which would make every test below
    # vacuously pass — the exact failure mode that let this bug class survive review 3 times.
    assert len(GATEWAY_ROUTES) > 25


def test_found_a_realistic_number_of_services():
    assert len(SERVICES) >= 10


@pytest.mark.parametrize("service_dir", SERVICES, ids=lambda d: d.name)
def test_every_registered_router_prefix_has_a_gateway_route(service_dir: pathlib.Path):
    prefixes = _extract_router_prefixes(service_dir)
    missing = prefixes - GATEWAY_ROUTES
    assert not missing, (
        f"{service_dir.name}: router prefix(es) {missing} are declared with APIRouter(prefix=...) "
        f"and registered in main.py's routers=[...], but have no entry in api-gateway's "
        f"proxy.py _ROUTES table — every request under {sorted(missing)} will 404 at the "
        f"gateway despite the backend fully implementing the feature. Add an entry to _ROUTES "
        f"(same fix as rl-agent/conditional-orders/options-income)."
    )


def test_market_data_router_count_matches_a_known_floor():
    # market-data owns the most routers by far (17 at time of writing) — a sudden drop would
    # mean the import-map parser silently broke on a new import style, not that routers
    # vanished. A floor, not an exact count, so adding new market-data routers over time
    # doesn't require touching this test.
    assert len(_extract_router_prefixes(SERVICES_DIR / "market-data")) >= 15

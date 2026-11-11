"""Launcher for a one-off production run of the options candidate study WITHOUT writing into
/app/src: copy this file, options_candidate_study.py and services/option_research_lead.py to /tmp/ocs/ in
the market-data container and run `python /tmp/ocs/launch.py OUT_DIR` from /app. It loads the study as
src.backtest.options_candidate_study, so its relative imports resolve to the DEPLOYED builder,
and records the SHA-256 of the deployed builder files and of the study itself beside the results.

Read-only: the study sets every transaction read-only. See
docs/features/2026-10-29-options-candidate-outcome-study-design.md, "Part A build".
"""
import hashlib
import importlib.util
import json
import sys

STUDY = "/tmp/ocs/options_candidate_study.py"
LEAD = "/tmp/ocs/option_research_lead.py"   # not in an image built before it existed


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def main(out: str) -> None:
    sys.path.insert(0, "/app")
    import src.backtest  # noqa: F401  (the packages the two modules belong to)
    import src.services  # noqa: F401
    _load("src.services.option_research_lead", LEAD)
    mod = _load("src.backtest.options_candidate_study", STUDY)

    from db import SessionLocal
    from sqlalchemy import text

    src_hash = {p: hashlib.sha256(open(f"/app/src/{p}", "rb").read()).hexdigest() for p in
                ("services/options_strategies.py", "services/uw_option_chain.py", "services/options_contract.py",
                 "api/routes.py")}
    src_hash["study"] = hashlib.sha256(open(STUDY, "rb").read()).hexdigest()
    src_hash["research_lead"] = hashlib.sha256(open(LEAD, "rb").read()).hexdigest()
    print(json.dumps({"source_sha256": src_hash}), flush=True)
    with SessionLocal() as s:
        syms = [r[0] for r in s.execute(text(
            "WITH RECURSIVE t AS ((SELECT symbol FROM option_chain_history ORDER BY symbol LIMIT 1) UNION ALL "
            "SELECT (SELECT symbol FROM option_chain_history WHERE symbol > t.symbol ORDER BY symbol LIMIT 1) "
            "FROM t WHERE t.symbol IS NOT NULL) SELECT symbol FROM t WHERE symbol IS NOT NULL"))]
        s.rollback()
        summary = mod.run(s, syms, out)
        with open(f"{out}/provenance.json", "w") as fh:
            json.dump({"source_sha256": src_hash, "study_version": mod.STUDY_VERSION}, fh, indent=1)
        print(json.dumps({"done": True, "trades": summary["trades"]}), flush=True)


if __name__ == "__main__":
    main(sys.argv[1])

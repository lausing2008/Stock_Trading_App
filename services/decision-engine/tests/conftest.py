"""No Docker-only dependencies to stub — scorer.py and hard_rejects.py are pure/dependency-
light (only pydantic + stdlib datetime/zoneinfo, both real and installed). hard_rejects.py's
one DB dependency (SessionLocal, for the macro-blackout check) is only reached when
reasons["macro_blackout"] is None — tests avoid it by always providing that key explicitly.
"""

# Options horizon/context validation and deployment

`validation.json`: final 864-test frontend suite, typecheck, unchanged backend boundary and final release identity.

`production.json`: all 15 containers healthy, expected prepared/running frontend build, five public HTTP 200 routes, and all 14 non-frontend start times unchanged from the preceding verified release.

`source.json`: all 12 backend src/shared digests match; no backend code changed in this update.

`served.json`: TSM/tracker pages serve horizon/holding controls, goal-fit-only label, context evidence and tracker entries from the expected build. This is HTTP/served-code verification; interactive authenticated browser acceptance was not run. New UI behavior is exercised by DOM regression tests.

All timestamps in raw evidence are UTC; the report also gives the America/Los_Angeles verification time. No provider quotes, user holdings, service credentials or tokens were added by this verification.

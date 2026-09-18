.PHONY: help build up down logs ps test fmt clean migrate seed test-all test-frontend

help:
	@echo "Stock Intelligence Platform"
	@echo "  make build      - Build all docker images"
	@echo "  make up         - Start the full stack"
	@echo "  make down       - Stop the stack"
	@echo "  make logs       - Tail logs"
	@echo "  make ps         - List services"
	@echo "  make test       - Run all unit tests"
	@echo "  make fmt        - Format Python code"
	@echo "  make migrate    - Run DB migrations"
	@echo "  make seed       - Seed stock universe"

build:
	docker compose -f docker/docker-compose.yml build

up:
	docker compose -f docker/docker-compose.yml --env-file .env up -d

down:
	docker compose -f docker/docker-compose.yml down

logs:
	docker compose -f docker/docker-compose.yml logs -f --tail=200

ps:
	docker compose -f docker/docker-compose.yml ps

# AUD-T400-CIHIDESFAILURE: `exit 1` inside the per-service SUBSHELL only exits that subshell.
# The for loop then continued, and a shell loop returns the status of its LAST iteration — so a
# failure in any service except the last one was silently discarded and `make test` exited 0.
# CI ran this target, so a genuinely red backend went green. Verified by reproducing the exact
# shell shape: a loop with a failing first iteration and a passing last one exits 0. It was in
# fact hiding 3 real research-engine failures at the time this was found.
#
# Now: run EVERY service (so one failure doesn't mask the rest — more useful than fail-fast for
# a suite this size), remember whether anything failed, and exit non-zero at the end. pytest's
# exit code 5 is "no tests collected", which stays a legitimate pass for a service without tests.
test:
	@fail=0; \
	for svc in market-data technical-analysis ml-prediction ranking-engine signal-engine strategy-engine portfolio-optimizer research-engine api-gateway decision-engine event-intelligence news-intelligence; do \
		echo "== $$svc =="; \
		(cd services/$$svc && python -m pytest -q); ec=$$?; \
		if [ "$$ec" -ne 0 ] && [ "$$ec" -ne 5 ]; then \
			echo "!! $$svc FAILED (pytest exit $$ec)"; \
			fail=1; \
		fi; \
	done; \
	if [ "$$fail" -ne 0 ]; then \
		echo "make test: one or more services failed"; \
		exit 1; \
	fi; \
	echo "make test: all services passed"

fmt:
	ruff format services shared
	ruff check --fix services shared

migrate:
	docker compose -f docker/docker-compose.yml exec market-data python -m src.db.migrate

seed:
	docker compose -f docker/docker-compose.yml exec market-data python -m src.services.seed_universe

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true

# AUD-A18-CIPRODBRANCH (2026-09-17): `make test` covers ONLY the 12 Python services. The
# frontend has its own 291-test vitest suite plus a typecheck, which CI runs as a SEPARATE
# job — so a local "make test: all services passed" is NOT the same green CI reports.
# That gap is not theoretical: it shipped a Tier 384 whose items rendered NOTHING (they were
# absent from TIER_LABEL/TIER_COLOR, so the render loop never visited them). The guard for it,
# tierLabelCoverage.test.ts, already existed and worked — it simply was never run locally.
# Use `make test-all` before pushing; it is what CI actually checks.
test-all: test test-frontend

test-frontend:
	@cd frontend && npx vitest run && npx tsc --noEmit -p tsconfig.json
	@echo "make test-frontend: frontend tests + typecheck passed"

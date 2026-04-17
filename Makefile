.PHONY: help build up down logs ps test fmt clean migrate seed

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

test:
	@for svc in market-data technical-analysis ml-prediction ranking-engine signal-engine strategy-engine portfolio-optimizer api-gateway; do \
		echo "== $$svc =="; \
		(cd services/$$svc && python -m pytest -q || exit 1); \
	done

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

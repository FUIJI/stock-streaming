COMPOSE = docker compose
LITE = docker compose -f docker-compose.prod.yml --profile local

.PHONY: up down restart logs ps test psql topics clean lite-up lite-down lite-logs backfill

up:
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

restart: down up

logs:
	$(COMPOSE) logs -f --tail=100

ps:
	$(COMPOSE) ps

test:
	cd ingestion && uv run pytest
	cd api && uv run pytest
	$(COMPOSE) run --rm --no-deps --entrypoint python3 spark-ohlcv -m pytest tests/ -q

psql:
	$(COMPOSE) exec timescaledb psql -U $${POSTGRES_USER:-stock} -d $${POSTGRES_DB:-stocks}

topics:
	$(COMPOSE) exec redpanda rpk topic list

# Wipe everything including data volumes
clean:
	$(COMPOSE) down -v

# ---------- Lite stack (no Redpanda/Spark/MinIO) ----------
lite-up:
	$(LITE) up -d --build

lite-down:
	$(LITE) down

lite-logs:
	$(LITE) logs -f --tail=100

# Historical 1m bars from Alpaca, e.g. make backfill DAYS=10
backfill:
	$(LITE) run --rm api python -m src.backfill --days $(or $(DAYS),5)

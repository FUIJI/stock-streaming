# Stock Streaming Platform — Project Charter

Real-time US stock streaming platform (portfolio project). Monorepo, one folder = one
service = one container. Full blueprint: `docs/project-structure-plan.md`.

## Architecture (data flow)

```
[Finnhub WS | Simulator] → ingestion → Redpanda (trades.raw)
    → processing/spark → TimescaleDB (ohlcv_1m + continuous aggregates)
                       → MinIO (bronze/silver Parquet)
    → api (FastAPI: REST /candles + WS /ws/{symbol}) → frontend (Next.js dashboard)
```

Lite stack (`docker-compose.prod.yml`, for ~1 GB VMs): no Redpanda/Spark/MinIO.
ingestion (`TRADE_SINK=api`) → api WS `/internal/ingest` (`TRADE_FEED=ingest`, bearer
`INGEST_TOKEN`) → in-memory candles (`api/src/aggregator.py`) → plain Postgres.

## Services

| Folder | Runtime | Notes |
|---|---|---|
| `ingestion/` | Python 3.12, uv | `TRADE_SOURCE=simulator\|finnhub\|alpaca` (simulator default, no key) · `TRADE_SINK=kafka\|api` |
| `processing/spark/` | PySpark, local[*] | 1-min event-time windows, 30s watermark, upsert via foreachBatch |
| `storage/timescale/init/` | SQL | Runs via /docker-entrypoint-initdb.d — only on FIRST boot of the volume; `make clean` to re-init. `001` must stay plain Postgres (lite stack mounts only it) |
| `storage/minio/` | sh | Bucket bootstrap (bronze/silver/gold) |
| `api/` | FastAPI, uv | REST reads TimescaleDB; WS consumes trades.raw via aiokafka (`TRADE_FEED=kafka`) or `/internal/ingest` (`ingest`). `CANDLE_ROLLUP=timescale\|sql` |
| `frontend/` | Next.js + TS | lightweight-charts candlesticks |

## Commands

- `make up` / `make down` / `make clean` (clean wipes volumes → re-runs SQL init)
- `make logs`, `make ps`, `make topics`, `make psql`
- Tests per service: `cd <service> && uv run pytest`; Spark tests run in-container:
  `docker compose run --rm --no-deps --entrypoint python3 spark-ohlcv -m pytest tests/ -q`
- Lite stack: `make lite-up` / `make lite-down` / `make lite-logs`; `make backfill DAYS=5` (Alpaca)
- No `make` on this machine? Use `docker compose up -d --build` etc. directly.
- Production (Oracle VM): `.github/workflows/deploy.yml` deploys on green CI on `master`;
  VM-side files live in `deploy/`; operations in `docs/runbook.md`. VM RAM is ~1 GB: keep
  `deploy/compose.vm.yml` mem_limits ≤ 512 MB total.
- uv.lock files are written by uv 0.6.3 (the Dockerfiles' version): relock with `uvx uv@0.6.3 lock`.

## Conventions

- All config via env vars (12-factor). `.env` never committed; keep `.env.example` current.
- Every Python service: uv + pyproject.toml, ruff, pytest, own Dockerfile + tests/.
- Kafka messages are JSON, keyed by symbol. Schema = Pydantic `Trade` in `ingestion/src/schemas.py`.
- Shell scripts and SQL must stay LF (enforced via .gitattributes) — they run inside Linux containers.
- Commit at the end of each working milestone.

## Ports (host)

Redpanda Kafka 19092 (internal: redpanda:9092) · Console 8080 · TimescaleDB 5432 ·
MinIO 9000/9001 · API 8000 · Frontend 3000

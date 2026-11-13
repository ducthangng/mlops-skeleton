.PHONY: eval bench test lint

eval:
	uv run python -m evals.run

bench:
	uv run python -m evals.bench

test:
	uv run --with pytest pytest -q tests

lint:
	uv run --with ruff ruff check app evals tests

# ---------- AI gateway (gateway/ + web/) ----------
.PHONY: gw-db gw-dev gw-web gw-test gw-build

gw-db:
	cd gateway && docker compose up -d postgres

gw-dev:
	cd gateway && uv run uvicorn gateway.main:app --port 8090 --reload

gw-web:
	cd web && npm run dev

gw-test:
	cd gateway && uv run pytest -q && uv run ruff check gateway tests examples
	cd web && npm run typecheck

gw-build:
	cd web && npm run build

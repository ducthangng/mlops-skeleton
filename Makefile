.PHONY: eval bench test lint

eval:
	uv run python -m evals.run

bench:
	uv run python -m evals.bench

test:
	uv run --with pytest pytest -q tests

lint:
	uv run --with ruff ruff check app evals tests

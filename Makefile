.PHONY: install install-research demo api web test lint build forward-init forward-status

install:
	uv sync --extra dev --extra research --extra operations
	npm --prefix apps/web install

install-research:
	uv sync --extra dev --extra research --extra operations

demo:
	uv run edgar-moe demo --output data/interim/synthetic-snapshot.json
	uv run python scripts/validate_snapshot.py data/interim/synthetic-snapshot.json

api:
	uv run uvicorn edgar_moe.api.app:app --reload --port 8000

web:
	npm --prefix apps/web run dev

test:
	uv run pytest
	npm --prefix apps/web run test

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy src scripts
	npm --prefix apps/web run lint

build:
	npm --prefix apps/web run build

forward-init:
	uv run edgar-moe forward-init

forward-status:
	uv run edgar-moe forward-status

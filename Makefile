.PHONY: install install-research demo api web test lint build

install:
	uv sync --extra dev --extra research
	npm --prefix apps/web install

install-research:
	uv sync --extra dev --extra research

demo:
	uv run edgar-moe demo --output data/demo/snapshot.json

api:
	uv run uvicorn edgar_moe.api.app:app --reload --port 8000

web:
	npm --prefix apps/web run dev

test:
	uv run pytest
	npm --prefix apps/web run test

lint:
	uv run ruff check .
	uv run mypy src
	npm --prefix apps/web run lint

build:
	npm --prefix apps/web run build

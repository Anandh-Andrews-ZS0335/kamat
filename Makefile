.PHONY: setup users seed up prod docker-up bank engine run evaluate test lint clean-runs

setup:        ## install Python 3.11 and all dependencies
	uv sync
	@test -f .env || cp .env.example .env

users:        ## create admin / manager / collector accounts with random passwords (paste output into .env)
	uv run python -m lastmile.api.auth users

seed:         ## regenerate both simulated banks (Riverbend and Harbor)
	uv run python -m bank_api seed
	uv run python -m bank_api seed --profile harbor

up:           ## start bank API (:8001) and engine + consoles (:8000) for local dev
	uv run python scripts/dev.py

prod:         ## start production multi-process server (0.0.0.0 binding)
	uv run python scripts/start_prod.py

docker-up:    ## build and start via Docker Compose
	docker compose up -d --build

harbor:       ## second bank API only (onboarding demo)
	uv run uvicorn bank_api.harbor:app --port 8002

bank:         ## bank API only
	uv run uvicorn bank_api.main:app --port 8001

engine:       ## engine API + consoles only
	uv run uvicorn lastmile.api.app:app --port 8000

run:          ## one full agent run from the terminal (bank API must be up)
	uv run python -m lastmile run

evaluate:     ## score a run against sealed truth: make evaluate RUN=run_...
	uv run python scripts/evaluate_against_truth.py --run-id $(RUN)

test:         ## the tests that protect the claims we make
	uv run pytest

lint:         ## style + architecture contracts
	uv run ruff check src scripts tests
	uv run lint-imports

clean-runs:   ## delete engine runs, audit and models (keeps bank data)
	rm -rf data/engine

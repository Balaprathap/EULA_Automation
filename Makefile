.PHONY: help install migrate migrate-local seed api worker test lint typecheck fe-install fe-lint fe-build fe-test up down eval

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-14s %s\n", $$1, $$2}'

install:      ## Install backend deps
	cd backend && pip install -e ".[dev]"

migrate:      ## Apply migrations to DATABASE_URL (Supabase)
	cd backend && python -m scripts.migrate

migrate-local: ## Apply migrations to a local/docker Postgres (adds auth+storage shim)
	cd backend && python -m scripts.migrate --local-shim

seed:         ## Seed default policy + categories (add DEMO=1 for demo data)
	cd backend && python -m scripts.seed

api:          ## Run API server
	cd backend && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

worker:       ## Run analysis worker
	cd backend && python -m app.worker

test:         ## Run backend tests
	cd backend && pytest -q

lint:         ## Lint backend
	cd backend && ruff check app scripts tests && ruff format --check app scripts tests

typecheck:    ## Type-check backend
	cd backend && mypy app

eval:         ## Measure retrieval recall@k against the labeled set
	cd backend && python -m scripts.evaluate_retrieval

fe-install:   ## Install frontend deps
	cd frontend && npm ci

fe-lint:      ## Lint frontend
	cd frontend && npm run lint

fe-build:     ## Production build
	cd frontend && npm run build

fe-test:      ## Frontend unit tests
	cd frontend && npm run test

up:           ## Start full local stack
	docker compose up --build

down:         ## Stop stack and remove volumes
	docker compose down -v

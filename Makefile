.DEFAULT_GOAL := help

# ===== Variables =====
DOCKER_COMPOSE := docker compose
UV := uv

# ===== Help =====
.PHONY: help
help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "} {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ===== Setup =====
.PHONY: install
install:  ## Install dependencies via uv
	$(UV) sync

.PHONY: install-dev
install-dev:  ## Install all dependencies including dev group
	$(UV) sync --all-groups

# ===== Infrastructure =====
.PHONY: up
up:  ## Start infra (postgres + redis)
	$(DOCKER_COMPOSE) up -d postgres redis
	@echo "Waiting for services to be healthy..."
	@$(DOCKER_COMPOSE) ps

.PHONY: down
down:  ## Stop infra
	$(DOCKER_COMPOSE) down

.PHONY: down-clean
down-clean:  ## Stop infra and remove volumes (WIPES DATA)
	$(DOCKER_COMPOSE) down -v

.PHONY: logs
logs:  ## Tail infra logs
	$(DOCKER_COMPOSE) logs -f

# ===== App =====
.PHONY: dev
dev:  ## Run dev server with hot reload
	$(UV) run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

.PHONY: run
run:  ## Run server without reload (production-like)
	$(UV) run uvicorn app.main:app --host 0.0.0.0 --port 8000

# ===== Database =====
.PHONY: migrate
migrate:  ## Apply migrations to head
	$(UV) run alembic upgrade head

.PHONY: migrate-down
migrate-down:  ## Rollback last migration
	$(UV) run alembic downgrade -1

.PHONY: migration
migration:  ## Generate new migration. Usage: make migration m="add users table"
	$(UV) run alembic revision --autogenerate -m "$(m)"

.PHONY: db-shell
db-shell:  ## Open psql shell to dev DB
# docker compose exec postgres bash
#docker compose exec postgres psql -U postgres -d court_booking tương đương
	docker exec -it court-booking-postgres psql -U postgres -d court_booking

# ===== Code quality =====
.PHONY: lint
lint:  ## Run ruff lint check
	$(UV) run ruff check .

.PHONY: format
format:  ## Format code with ruff
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

.PHONY: format-check
format-check:  ## Check formatting without changes (CI)
	$(UV) run ruff format --check .

# ===== Tests =====
.PHONY: test
test:  ## Run all tests
	$(UV) run pytest

.PHONY: test-cov
test-cov:  ## Run tests with coverage report
	$(UV) run pytest --cov=app --cov-report=term-missing --cov-report=html

.PHONY: test-fast
test-fast:  ## Skip slow tests (concurrency)
	$(UV) run pytest -m "not concurrency"

# ===== Docker =====
.PHONY: build
build:  ## Build app Docker image
	docker build -t court-booking-api:latest .

# ===== Aggregate =====
.PHONY: check
check: lint format-check test  ## Run full CI checks locally

.PHONY: clean
clean:  ## Remove caches
	rm -rf .pytest_cache .ruff_cache .coverage htmlcov
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
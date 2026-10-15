.PHONY: up down logs ps migrate revision test lint backup bot

# Локальная разработка: python -m venv .venv && pip install -e ".[dev]"

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f --tail=200 app

ps:
	docker compose ps

migrate:
	alembic upgrade head

revision:
	alembic revision --autogenerate -m "$(m)"

test:
	pytest -q

lint:
	ruff check src tests

backup:
	bash scripts/backup.sh

# Локальный запуск бота процессом (Postgres — из compose)
bot:
	python -m jarvis.main

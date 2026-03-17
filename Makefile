.PHONY: dev build run stop clean test

dev:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8001

build:
	docker compose build

run:
	docker compose up -d

stop:
	docker compose down

clean:
	docker compose down -v

test:
	uv run pytest -v

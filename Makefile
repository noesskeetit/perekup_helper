.PHONY: dev build run stop clean

dev:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

build:
	docker compose build

run:
	docker compose up -d

stop:
	docker compose down

clean:
	docker compose down -v

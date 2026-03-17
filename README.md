# perekup_helper

AI-агрегатор для перекупов: парсинг авто-объявлений, анализ цен ниже рынка, AI-категоризация

## Быстрый старт

```bash
# Скопировать переменные окружения
cp .env.example .env

# Локальная разработка (без Docker)
make dev

# Сборка и запуск через Docker Compose
make build
make run

# Остановка
make stop
```

## Структура проекта

```
app/
├── api/        # Эндпоинты FastAPI
├── models/     # SQLAlchemy модели
├── services/   # Бизнес-логика
├── parsers/    # Парсеры объявлений
└── main.py     # Точка входа приложения
```

## Стек

- Python 3.12, FastAPI
- PostgreSQL 16, Redis 7
- Docker Compose

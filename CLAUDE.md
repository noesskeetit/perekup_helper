# PerekupHelper

AI-агрегатор для автоперекупов. Парсит объявления с Авито/Авто.ру, анализирует через Claude API, показывает выгодные предложения ниже рынка.

## Stack

- Python 3.11+, FastAPI, uvicorn
- PostgreSQL (async via SQLAlchemy + asyncpg), Redis
- Alembic for migrations
- aiogram 3 (Telegram bot)
- anthropic SDK (AI categorization)
- httpx + BeautifulSoup4 (parsing)
- Docker Compose for local infra

## Project structure

```
app/                    # FastAPI web application
  main.py               # App entrypoint, lifespan, routes
  config.py             # Pydantic settings
  database.py           # DB connection setup
  db/session.py          # Async session factory
  models/               # SQLAlchemy ORM models (Listing, ListingAnalysis)
  routers/              # API endpoints (listings, stats)
  routes/               # HTML page routes (dashboard)
  schemas.py            # Pydantic request/response schemas
  templates/            # Jinja2 HTML templates for dashboard
  static/               # CSS

avito_parser/           # Avito scraper module
  listing_parser.py     # Main parser logic
  card_parser.py        # Individual card parsing
  price_analyzer.py     # Market price comparison
  pipeline.py           # Full parse pipeline
  http_client.py        # HTTP client with rate limiting

perekup_helper/         # AI categorization module
  categorizer.py        # Claude API integration for description analysis
  batch.py              # Batch processing
  models.py             # Categorization data models

bot/                    # Telegram bot
  main.py               # Bot entrypoint
  handlers/             # Command handlers (start, filters, stats)
  services/             # Checker, notifier
  db/                   # Bot-specific DB models

tests/                  # pytest tests
```

## Commands

```bash
# Local dev (requires PostgreSQL and Redis running)
make dev

# Docker Compose (full stack)
make build && make run

# Stop
make stop

# Run tests
pytest

# Lint
ruff check app/ bot/ avito_parser/ perekup_helper/ tests/
ruff format app/ bot/ avito_parser/ perekup_helper/ tests/

# Migrations
alembic upgrade head
alembic revision --autogenerate -m "description"
```

## Environment variables

See `.env.example`. Key vars:
- `DATABASE_URL` — PostgreSQL connection string
- `REDIS_URL` — Redis connection string
- `ANTHROPIC_API_KEY` — for AI categorization
- `BOT_TOKEN` — Telegram bot token

## Conventions

- Use `ruff` for linting and formatting (configured in pyproject.toml)
- All new code must pass `ruff check` before commit
- Run `pytest` before creating a PR — all tests must pass
- Use async SQLAlchemy for DB operations in app/
- Type hints are encouraged but mypy is non-blocking in CI
- Write tests in tests/ for new functionality
- One feature per PR, keep PRs focused

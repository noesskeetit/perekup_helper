FROM python:3.11-slim

WORKDIR /app

# System deps for curl_cffi (Auto.ru parser) and psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libffi-dev && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY pyproject.toml .
RUN uv pip install --system .

COPY . .

# Create data directory for bot SQLite DB
RUN mkdir -p /app/data /app/models

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

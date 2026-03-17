FROM python:3.11-slim AS base

WORKDIR /app

COPY pyproject.toml ./
COPY src/ ./src/

RUN pip install --no-cache-dir .

FROM base AS runtime
CMD ["python", "-m", "perekup_helper"]

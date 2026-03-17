FROM python:3.11-slim AS base

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir .

COPY src/ ./src/

FROM base AS runtime
CMD ["python", "-m", "perekup_helper"]

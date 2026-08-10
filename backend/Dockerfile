FROM python:3.12-slim

WORKDIR /app

# psycopg2 build deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run migrations automatically before starting (safe because it's idempotent)
CMD ["sh", "-c", "alembic upgrade head && python run_api.py"]

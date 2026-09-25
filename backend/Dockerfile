FROM python:3.12-slim

WORKDIR /app

# psycopg2 build deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r ./backend/requirements.txt

COPY pyproject.toml ./pyproject.toml
COPY alembic.ini ./alembic.ini
COPY backend ./backend
COPY oipulse ./oipulse

RUN pip install --no-cache-dir -e .
ENV PYTHONPATH=/app:/app/backend

WORKDIR /app/backend

CMD ["sh", "-c", "cd /app && alembic upgrade head && cd /app/backend && python run_api.py"]


FROM python:3.12-slim

WORKDIR /app

# psycopg2 build deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Start API server
CMD ["python", "run_api.py"]

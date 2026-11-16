FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libcurl4 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .

ENV HOST=0.0.0.0 \
    PORT=10000 \
    PYTHONUNBUFFERED=1

EXPOSE 10000

CMD ["python", "-m", "app"]

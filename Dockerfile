FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir prometheus_client psycopg2-binary requests

COPY simulator/ simulator/
COPY classifier/ classifier/

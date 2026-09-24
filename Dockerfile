FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent/ ./agent/
COPY api/ ./api/
COPY extraction/ ./extraction/
COPY db/ ./db/

EXPOSE 8080

CMD exec uvicorn api.app:app --host 0.0.0.0 --port ${PORT:-8080}
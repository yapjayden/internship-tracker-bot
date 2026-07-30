FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY core/ core/
COPY bot/ bot/

# Cloud Run injects $PORT; default to 8080 for local runs.
ENV PORT=8080
CMD exec uvicorn bot.app:app --host 0.0.0.0 --port ${PORT}

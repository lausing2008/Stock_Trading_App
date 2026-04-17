FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends build-essential libpq-dev curl && rm -rf /var/lib/apt/lists/*
COPY services/ranking-engine/requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt
COPY shared /app/shared
ENV PYTHONPATH=/app/shared:/app
COPY services/ranking-engine /app
EXPOSE 8004
HEALTHCHECK --interval=30s --timeout=5s CMD curl -fs http://localhost:8004/health || exit 1
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8004"]

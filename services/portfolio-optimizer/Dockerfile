FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends build-essential libpq-dev curl && rm -rf /var/lib/apt/lists/*
COPY services/portfolio-optimizer/requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt
COPY shared /app/shared
ENV PYTHONPATH=/app/shared:/app
COPY services/portfolio-optimizer /app
EXPOSE 8007
HEALTHCHECK --interval=30s --timeout=5s CMD curl -fs http://localhost:8007/health || exit 1
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8007"]

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libimage-exiftool-perl libheif1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app/ ./app/
COPY prompts/ ./prompts/
COPY migrations/ ./migrations/

RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /data/stock \
    && chown -R appuser:appuser /app /data/stock
USER appuser

EXPOSE 8000
CMD ["uvicorn", "stock_pipeline.main:app", "--host", "0.0.0.0", "--port", "8000"]

# Python 3.12 (not 3.14) for broad wheel availability across the stack.
FROM python:3.12-slim AS api

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# Install deps first for layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY dashboard ./dashboard

EXPOSE 8000

# Healthcheck mirrors the on-call check. Honors $PORT (Render) but defaults to
# 8000 for local docker-compose.
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request,sys; urllib.request.urlopen('http://localhost:%s/health' % os.getenv('PORT','8000')); sys.exit(0)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

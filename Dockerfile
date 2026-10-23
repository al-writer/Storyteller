# syntax=docker/dockerfile:1
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/data

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /data \
    && chown app:app /data

COPY app ./app
COPY data/lexicon.jsonl ./data/lexicon.jsonl
COPY init_words.py pyproject.toml ./

USER app
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request, sys; sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).read() != b'ok')"]

# Один воркер: SQLite и лимиты запросов живут в памяти одного процесса.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]


FROM runtime AS test

USER root
COPY requirements-test.txt .
RUN pip install -r requirements-test.txt
COPY tests ./tests
RUN chown -R app:app /app/tests
USER app
ENV DATA_DIR=/tmp/storyteller-test
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]

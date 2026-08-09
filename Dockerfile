FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin app

COPY pyproject.toml README.md ./
COPY src ./src
COPY scripts ./scripts
COPY accounts.example.json ./accounts.example.json

RUN python -m pip install --upgrade pip \
    && python -m pip install . \
    && mkdir -p /app/data /app/.auth \
    && chown -R app:app /app

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()"]

CMD ["uvicorn", "qwen2api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY science/pyproject.toml ./science/pyproject.toml
COPY science/sphx_core ./science/sphx_core
COPY backend/pyproject.toml ./backend/pyproject.toml
COPY backend/alembic.ini ./backend/alembic.ini
COPY backend/app ./backend/app
COPY tests ./tests

RUN pip install --no-cache-dir --upgrade pip setuptools \
    && pip install --no-cache-dir ./science ./backend

RUN useradd --create-home --shell /bin/sh appuser
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request, os; urllib.request.urlopen(f'http://localhost:{os.environ.get(\"PORT\", 8000)}/health')"

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips=*"]

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY science/pyproject.toml ./science/pyproject.toml
COPY backend/pyproject.toml ./backend/pyproject.toml

RUN pip install --no-cache-dir --upgrade pip setuptools \
    && pip install --no-cache-dir \
        "numpy>=1.26" "scipy>=1.12" "astropy>=6.0" "astropy-healpix>=1.0" \
        "photutils>=1.12" "sep>=1.2" "reproject>=0.13" "astroquery>=0.4.7" \
        "pyvo>=1.5" "scikit-learn>=1.4" "emcee>=3.1" \
        "fastapi>=0.111" "uvicorn[standard]>=0.30" "pydantic>=2.7" \
        "pydantic-settings>=2.2" "sqlalchemy>=2.0" "alembic>=1.13" \
        "psycopg[binary]>=3.1" "redis>=5.0" "celery[redis]>=5.4" \
        "boto3>=1.34" "httpx>=0.27" "pyyaml>=6.0" "argon2-cffi>=23.1" "python-multipart>=0.0.9" \
        "matplotlib>=3.8"

COPY science/sphx_core ./science/sphx_core
COPY backend/alembic.ini ./backend/alembic.ini
COPY backend/app ./backend/app
COPY tests ./tests

RUN pip install --no-cache-dir -e ./science -e ./backend

RUN useradd --create-home --shell /bin/sh appuser
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request, os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", 8000)}/health')"

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips=*"]

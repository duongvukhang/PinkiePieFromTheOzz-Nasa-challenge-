FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY science/pyproject.toml ./science/pyproject.toml
COPY science/sphx_core ./science/sphx_core
COPY backend/pyproject.toml ./backend/pyproject.toml
COPY backend/app ./backend/app

RUN pip install --no-cache-dir --upgrade pip setuptools \
    && pip install --no-cache-dir -e ./science -e ./backend

RUN useradd --create-home --shell /bin/sh appuser
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

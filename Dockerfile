FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_NO_INTERACTION=1

WORKDIR /app

RUN pip install --upgrade pip setuptools wheel poetry

COPY pyproject.toml poetry.lock ./
RUN poetry install --only main --no-root --no-ansi

COPY . .

RUN chmod +x docker/app-entrypoint.sh

EXPOSE 5234

ENTRYPOINT ["./docker/app-entrypoint.sh"]
CMD ["gunicorn", "--bind", "0.0.0.0:5234", "--workers", "1", "--threads", "8", "--worker-class", "gthread", "flymanager.app.wsgi:app"]
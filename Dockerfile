FROM python:3.11-slim

ARG APP_VERSION=dev

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_NO_INTERACTION=1

ENV APP_VERSION=${APP_VERSION}

WORKDIR /app

RUN pip install --upgrade pip setuptools wheel poetry

COPY pyproject.toml poetry.lock ./
RUN poetry install --only main --no-root --no-ansi

COPY . .

RUN chmod +x docker/app-entrypoint.sh docker/worker-entrypoint.sh

EXPOSE 5234

# Default (web) entrypoint/command. The worker service in compose.*.yaml
# overrides both to run docker/worker-entrypoint.sh + the RQ worker instead.
ENTRYPOINT ["./docker/app-entrypoint.sh"]
CMD ["gunicorn", "--bind", "0.0.0.0:5234", "--workers", "1", "--threads", "12", "--worker-class", "gthread", "--timeout", "300", "--access-logfile", "-", "--error-logfile", "-", "flymanager.app.wsgi:app"]

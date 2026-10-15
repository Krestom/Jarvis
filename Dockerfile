FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

COPY alembic.ini ./
COPY alembic ./alembic
COPY docker/entrypoint.sh /usr/local/bin/jarvis-entrypoint
# Не запускаем от root; каталоги данных и выгрузок принадлежат процессу.
RUN chmod +x /usr/local/bin/jarvis-entrypoint \
    && useradd --system --create-home --uid 10001 jarvis \
    && mkdir -p /data/documents /data/exports \
    && chown -R jarvis:jarvis /data

USER jarvis

EXPOSE 8080

ENTRYPOINT ["jarvis-entrypoint"]
CMD ["python", "-m", "jarvis.main"]

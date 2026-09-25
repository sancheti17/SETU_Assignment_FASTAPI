FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install -r requirements.txt \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --create-home app
COPY --chown=10001:10001 . /app
RUN mkdir -p /app/data && chown 10001:10001 /app/data
USER 10001:10001
ENV DATABASE_PATH=/app/data/payments.db PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "exec python -m uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]

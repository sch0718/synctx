FROM python:3.12-slim

WORKDIR /app

COPY shared/synctx-common /app/shared/synctx-common
COPY services/cdn /app/services/cdn
COPY pyproject.toml /app/

RUN pip install --no-cache-dir uv && \
    uv pip install --system -e shared/synctx-common -e services/cdn

ENV PYTHONPATH=/app/services/cdn/src
ENV PORT=8000

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]

FROM python:3.13.16-slim-trixie

WORKDIR /app

COPY shared/synctx-common /app/shared/synctx-common
COPY services/collab-gateway /app/services/collab-gateway
COPY pyproject.toml /app/

RUN pip install --no-cache-dir uv && \
    uv pip install --system -e shared/synctx-common -e services/collab-gateway

ENV PYTHONPATH=/app/services/collab-gateway/src
ENV PORT=8000

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]

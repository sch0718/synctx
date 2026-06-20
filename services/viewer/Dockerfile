FROM python:3.12-slim

WORKDIR /app

# Install git for repo cloning
RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

COPY shared/synctx-common /app/shared/synctx-common
COPY services/viewer /app/services/viewer
COPY pyproject.toml /app/

RUN pip install --no-cache-dir uv && \
    uv pip install --system -e shared/synctx-common -e services/viewer

ENV PYTHONPATH=/app/services/viewer/src
ENV PORT=8000

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]

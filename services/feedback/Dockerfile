FROM python:3.12-slim

WORKDIR /app

COPY shared/synctx-common /app/shared/synctx-common
COPY services/feedback /app/services/feedback
COPY pyproject.toml /app/

RUN pip install --no-cache-dir uv && \
    uv pip install --system -e shared/synctx-common -e services/feedback

ENV PYTHONPATH=/app/services/feedback/src

CMD ["synctx-feedback"]

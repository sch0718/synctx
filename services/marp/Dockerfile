ARG NODE_IMAGE=node:20-slim
ARG PYTHON_IMAGE=python:3.12-slim

FROM ${NODE_IMAGE} AS node-env
RUN npm install -g @marp-team/marp-cli @mermaid-js/mermaid-cli

FROM ${PYTHON_IMAGE}

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends git chromium fonts-noto-cjk && \
    rm -rf /var/lib/apt/lists/*

COPY --from=node-env /usr/local/bin/node /usr/local/bin/
COPY --from=node-env /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf /usr/local/lib/node_modules/@marp-team/marp-cli/marp-cli.js /usr/local/bin/marp && \
    ln -sf /usr/local/lib/node_modules/@mermaid-js/mermaid-cli/src/cli.js /usr/local/bin/mmdc && \
    ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm

ENV CHROME_PATH=/usr/bin/chromium
ENV PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium

COPY shared/synctx-common /app/shared/synctx-common
COPY services/marp /app/services/marp
COPY pyproject.toml /app/

RUN pip install --no-cache-dir uv && \
    uv pip install --system -e shared/synctx-common -e services/marp

ENV PYTHONPATH=/app/services/marp/src
ENV PORT=8000

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]

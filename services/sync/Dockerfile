ARG NODE_IMAGE=node:20-slim
ARG PYTHON_IMAGE=python:3.12-slim

FROM ${NODE_IMAGE} AS node-env
RUN npm install -g @mermaid-js/mermaid-cli

FROM ${PYTHON_IMAGE}

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends git chromium && \
    rm -rf /var/lib/apt/lists/*

COPY --from=node-env /usr/local/bin/node /usr/local/bin/
COPY --from=node-env /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf /usr/local/lib/node_modules/.bin/mmdc /usr/local/bin/mmdc && \
    ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm

ENV CHROME_BIN=/usr/bin/chromium
ENV PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium

COPY shared/synctx-common /app/shared/synctx-common
COPY services/sync /app/services/sync
COPY services/sync/puppeteer-config.json /puppeteer-config.json
COPY pyproject.toml /app/

RUN pip install --no-cache-dir uv && \
    uv pip install --system -e shared/synctx-common -e services/sync

ENV PYTHONPATH=/app/services/sync/src

CMD ["synctx-sync"]

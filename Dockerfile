FROM python:3.12-slim-bookworm AS assets
ARG CODEX_VERSION=0.160.0
ENV CODEX_VERSION=${CODEX_VERSION}
COPY download-assets.py /tmp/download-assets.py
RUN python /tmp/download-assets.py

FROM python:3.12-slim-bookworm
ARG CODEX_VERSION=0.160.0
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 CODEX_VERSION=${CODEX_VERSION} HOME=/home/codex CODEX_HOME=/home/codex/.codex WORKSPACE=/workspace
RUN apt-get update && apt-get install -y --no-install-recommends bash git curl ripgrep ca-certificates nodejs npm && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 -s /bin/bash codex && mkdir -p /workspace /home/codex/.codex && chown -R codex:codex /workspace /home/codex
WORKDIR /app
COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY --from=assets /opt/codex /opt/codex
RUN ln -s /opt/codex/bin/codex /usr/local/bin/codex
COPY --from=assets /app/static/vendor /app/static/vendor
COPY server.py .
COPY static/ static/
USER codex
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2)"
CMD ["python", "server.py"]

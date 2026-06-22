# OpsWatch runs on the Python standard library alone, so there are no third-party
# packages to install and nothing to audit. The image is small and the build is
# fast.
FROM python:3.12-slim

WORKDIR /app

COPY opswatch/ ./opswatch/
COPY datasets/ ./datasets/
COPY config.example.json ./config.example.json
# Ship a small, self-contained config as the default so the container shows a
# live dashboard the moment it starts. Mount your own over /app/config.json
# (see docker-compose.yml) to run your real jobs and monitors.
COPY config.docker.json ./config.json

# Bind the dashboard so it is reachable from outside the container, and keep the
# database and runtime files on a mounted volume so they survive restarts.
# Override any of these at run time.
ENV OPSWATCH_DASHBOARD_HOST=0.0.0.0 \
    OPSWATCH_DASHBOARD_PORT=8765 \
    OPSWATCH_STORE_PATH=/data/opswatch.db \
    OPSWATCH_STATE_DIR=/data \
    PYTHONUNBUFFERED=1

VOLUME ["/data"]
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/healthz',timeout=2).read()==b'ok' else 1)"

CMD ["python", "-m", "opswatch", "--config", "/app/config.json"]

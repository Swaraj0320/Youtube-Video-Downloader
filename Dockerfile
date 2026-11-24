# Backend only. Vercel builds frontend/ separately.
FROM node:24.18.0-bookworm-slim@sha256:6f7b03f7c2c8e2e784dcf9295400527b9b1270fd37b7e9a7285cf83b6951452d AS node
FROM python:3.12-slim-bookworm@sha256:44cf1793efa7c7a476bb0f2cff81443f4a072c90c1cb127e2924933daa3e8c64
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/backend \
    TUBECLIP_ENV=production \
    TUBECLIP_DATA_DIR=/tmp/tubeclip-data \
    OMP_NUM_THREADS=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates libstdc++6 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 tubeclip
COPY --from=node /usr/local/bin/node /usr/local/bin/node
WORKDIR /app
COPY backend/requirements.txt backend/requirements.lock.txt ./backend/
RUN pip install --no-cache-dir --only-binary=:all: -r backend/requirements.txt \
    && pip check \
    && python -c "import fastapi, uvicorn, yt_dlp; import importlib.metadata as m; print(m.version('yt-dlp-ejs'))" \
    && node --version && ffmpeg -version && ffprobe -version
COPY backend/app ./backend/app
USER tubeclip
# Offline: validate EJS hashes and the restricted Node execution mode as runtime user.
RUN python -m app.extraction_probe --self-check
EXPOSE 10000
# One process/instance: jobs and quotas are intentionally in memory for a personal demo.
CMD ["sh", "-c", "exec python -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port \"${PORT:-10000}\" --workers 1 --limit-concurrency 32 --no-proxy-headers --no-access-log"]

# Backend only. Vercel builds frontend/ separately.
FROM node:24-bookworm-slim AS node
FROM python:3.12-slim-bookworm
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
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt \
    && python -c "import fastapi, uvicorn, yt_dlp; import importlib.metadata as m; print(m.version('yt-dlp-ejs'))" \
    && node --version && ffmpeg -version && ffprobe -version
COPY backend/app ./backend/app
USER tubeclip
EXPOSE 10000
# One process/instance: jobs and quotas are intentionally in memory for a personal demo.
CMD ["sh", "-c", "exec python -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port \"${PORT:-10000}\" --workers 1 --limit-concurrency 32 --no-proxy-headers --no-access-log"]

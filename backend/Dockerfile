# QuantFolio API service and worker job (one image, two entrypoints).
# Build context: the repository root (see cloudbuild.yaml / scripts\gcp\build.ps1):
#   gcloud builds submit --config backend/cloudbuild.yaml .
# API (Cloud Run service):  python -m quantfolio.cloud.serve   (default CMD, listens on 0.0.0.0:$PORT)
# Worker (Cloud Run job):   python -m quantfolio.cloud.worker
# Nothing secret is copied in: .env files, keys, IBM tokens, uploads and run outputs are excluded by
# .dockerignore / .gcloudignore, and secrets reach the container only as Secret Manager env vars.

# python:3.13-slim-bookworm, pinned by digest (multi-arch index; resolved 2026-10-09).
FROM python:3.13-slim-bookworm@sha256:a1165e272e578941b84abc79e4ab38a0305cd12803a5c4247979ac7655f4d641

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app/backend

# Dependencies first (cached layer). Every package is pinned with a hash and installed from a wheel only.
COPY backend/requirements.cloud.txt ./requirements.cloud.txt
# `pip check` re-evaluates every dependency marker on Linux, so a lock resolved elsewhere cannot miss a package.
RUN python -m pip install --require-hashes --only-binary=:all: --no-deps -r requirements.cloud.txt \
 && python -m pip check \
 && python -c "import qiskit, qiskit_aer, fastapi, google.cloud.firestore, google.cloud.storage, jwt; print('deps ok')"

# Unprivileged runtime user.
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --home-dir /app --no-create-home --shell /usr/sbin/nologin app

# Application code (read-only for the app user) and the bundled market data the app runs offline from.
COPY backend/quantfolio ./quantfolio
COPY data/cache/datasets/default.txt /app/data/cache/datasets/default.txt
COPY data/cache/datasets/nifty50-yahoo-aaa53c0c94ef.parquet data/cache/datasets/nifty50-yahoo-aaa53c0c94ef.close.parquet data/cache/datasets/nifty50-yahoo-aaa53c0c94ef.json /app/data/cache/datasets/
COPY data/examples /app/data/examples
COPY data/universe /app/data/universe

# Uploaded datasets are written next to the bundled one (then pushed to the private bucket); job state and run
# artifacts live in /tmp (Cloud Run's in-memory filesystem). Durable state is in Firestore and Cloud Storage.
RUN mkdir -p /tmp/qf/state /tmp/qf/runs \
 && chown -R app:app /app/data/cache /tmp/qf \
 && python -m compileall -q /app/backend/quantfolio

ENV PYTHONPATH=/app/backend \
    QF_DATA_DIR=/app/data \
    QF_STATE_DIR=/tmp/qf/state \
    QF_RUNS_DIR=/tmp/qf/runs \
    QF_FRONTEND_DIST=/nonexistent \
    QF_START_WORKER=false \
    QF_WARM_WORKER=false \
    QF_LOG_LEVEL=INFO \
    HOME=/tmp

USER 10001:10001
EXPOSE 8080
CMD ["python", "-m", "quantfolio.cloud.serve"]

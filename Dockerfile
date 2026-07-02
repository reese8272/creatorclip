FROM python:3.12-slim AS base

# fontconfig + the Anton TTF are required by libass for animated-caption rendering
# (Issue 133). Without fontconfig + an `fc-cache -f`, libass silently falls back to a
# default font and the rendered Bold Pop / Gradient Slide captions look nothing like
# the intended style. Anton (Google Fonts, SIL OFL) is the canonical MrBeast-style
# condensed sans for short-form captions.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    ffmpeg \
    fontconfig \
    fonts-open-sans \
    fonts-dejavu-core \
    wget \
    ca-certificates \
    && mkdir -p /usr/share/fonts/custom \
    && wget -q -O /usr/share/fonts/custom/Anton-Regular.ttf \
       https://raw.githubusercontent.com/google/fonts/main/ofl/anton/Anton-Regular.ttf \
       || echo "Anton font fetch failed — libass falls back to fonts-open-sans" \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*

# MediaPipe Tasks-compatible BlazeFace model for the gated per-frame reframe path
# (Issue 189 / 352 Batch H). The Tasks FaceDetector rejects the legacy Solutions
# .tflite bundled inside the mediapipe pip package, so we ship the hub asset at a
# pinned path and point MEDIAPIPE_FACE_MODEL_PATH at it. Fetch failure is non-fatal:
# the reframe path falls back to frame-center (and the flag defaults to off).
RUN mkdir -p /usr/share/mediapipe-models \
    && wget -q -O /usr/share/mediapipe-models/blaze_face_short_range.tflite \
       https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/latest/blaze_face_short_range.tflite \
       || { rm -f /usr/share/mediapipe-models/blaze_face_short_range.tflite; \
            echo "BlazeFace model fetch failed — reframe falls back to frame center"; }
ENV MEDIAPIPE_FACE_MODEL_PATH=/usr/share/mediapipe-models/blaze_face_short_range.tflite

WORKDIR /app

# ── Dependency layer (cached until requirements.txt changes) ─────────────────
FROM base AS builder
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# ── Frontend build (Vite SPA → /app/frontend/dist) ───────────────────────────
# The React + TS app (frontend/, base=/app/) is compiled here and the static
# bundle is copied into the runtime image. main.py serves it under /app/* and
# no-ops if the bundle is absent, so this stage is what makes the SPA live in
# prod. npm ci layer is cached until package-lock.json changes. See
# docs/DECISIONS.md (2026-06-17).
FROM node:22-slim AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ── Runtime image ─────────────────────────────────────────────────────────────
FROM base AS runtime
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH
# /app holds first-party packages (dna/, worker/, youtube/, …). WORKDIR alone
# does not put it on sys.path for processes whose entry point is a script in
# /root/.local/bin (e.g. `celery …`) — sys.path[0] becomes the script's dir,
# not CWD. Forked Celery pool workers then hit ModuleNotFoundError on lazy
# first-party imports (Prod incident 2026-05-30: build_dna). Setting
# PYTHONPATH guarantees /app is discoverable for every process in the image.
ENV PYTHONPATH=/app

# Cache-busting version stamp for `/static/*.css` and `/static/*.js`. Set by
# the CI build step from the short git SHA — see .github/workflows/docker-publish.yml.
# Defaults to "dev" so local `docker build` without --build-arg still works.
ARG GIT_SHA=dev
ENV STATIC_VERSION=$GIT_SHA

COPY . .
# Overlay the compiled SPA on top of the copied source tree. .dockerignore keeps
# the local frontend/dist + node_modules out of the build context, so this is the
# only frontend/dist that lands in the image.
COPY --from=frontend-build /frontend/dist ./frontend/dist

EXPOSE 8000

# Dev default — override in docker-compose or production deploy:
#   Production: gunicorn -k uvicorn.workers.UvicornWorker -w 4 main:app
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

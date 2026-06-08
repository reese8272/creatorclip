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

WORKDIR /app

# ── Dependency layer (cached until requirements.txt changes) ─────────────────
FROM base AS builder
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

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

COPY . .

EXPOSE 8000

# Dev default — override in docker-compose or production deploy:
#   Production: gunicorn -k uvicorn.workers.UvicornWorker -w 4 main:app
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

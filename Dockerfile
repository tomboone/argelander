# One image, one package: every Compose service runs `python -m argelander <command>`
# (spec §3). Stages: `base` holds the runtime dependencies, `dev` adds the dev group (pytest,
# ruff, ty) for the hooks and tasks, `jupyter` adds JupyterLab for the prototype notebooks,
# and `prod` comes last so an untargeted `docker build .` produces the prod image.
FROM python:3.12-slim AS base

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# The venv lives outside /app so the dev bind mount of the repo cannot hide it.
# VIRTUAL_ENV tells ty which interpreter to resolve imports against.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    VIRTUAL_ENV=/opt/venv \
    UV_CACHE_DIR=/tmp/uv-cache \
    UV_LINK_MODE=copy \
    PATH="/opt/venv/bin:$PATH" \
    MPLBACKEND=Agg

# astropy, astroquery and lightkurve all derive their download caches from XDG_CACHE_HOME
# (verified 2026-10-19 against astropy 8.0, astroquery 0.4.11, lightkurve 2.6);
# ASTROPY_CACHE_DIR would miss lightkurve. This needs astropy>=8.0: earlier versions used
# XDG_CACHE_HOME only when the subdirectory already existed. Matplotlib's font cache lands
# there too. A
# Compose file that moves ASTRO_CACHE_DIR must set XDG_CACHE_HOME to the same path.
ENV ASTRO_CACHE_DIR=/app/data/astropy
ENV XDG_CACHE_HOME=$ASTRO_CACHE_DIR

RUN useradd --create-home --uid 1000 argelander \
    && mkdir -p /app/data /opt/venv /tmp/uv-cache \
    && chown -R argelander:argelander /app /opt/venv /tmp/uv-cache

WORKDIR /app
USER argelander

# Dependencies first, so a source change does not reinstall them.
COPY --chown=argelander:argelander pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/tmp/uv-cache,uid=1000 \
    uv sync --frozen --no-dev --no-install-project

COPY --chown=argelander:argelander README.md ./
COPY --chown=argelander:argelander src ./src
RUN --mount=type=cache,target=/tmp/uv-cache,uid=1000 \
    uv sync --frozen --no-dev

CMD ["python", "-m", "argelander", "version"]

FROM base AS dev

RUN --mount=type=cache,target=/tmp/uv-cache,uid=1000 \
    uv sync --frozen

FROM dev AS jupyter

RUN --mount=type=cache,target=/tmp/uv-cache,uid=1000 \
    uv sync --frozen --group notebook

# No token or password: Compose publishes this port on 127.0.0.1 only.
CMD ["jupyter", "lab", "--ip=0.0.0.0", "--port=8888", "--no-browser", \
     "--IdentityProvider.token=", "--ServerApp.password=", \
     "--ServerApp.root_dir=/app/notebooks"]

FROM base AS prod

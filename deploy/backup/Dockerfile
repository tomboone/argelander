# The `backup` service (spec §10): pg_dump piped into restic, nightly at 03:30 local, run by
# supercronic. Its own small image rather than a stage of the app's: it needs none of the
# package, and the app image needs none of this.
FROM debian:trixie-slim

# pg_dump has to be at least the server's major version, and its output restores cleanly only
# into a server at least as new as the dump tool: a pg_dump 17 writes `SET transaction_timeout`,
# which a Postgres 16 restore rejects. Debian ships 17, so the client comes from the PGDG
# repository, pinned to the server's 16. Move it with compose.yaml's postgres image.
ARG PG_MAJOR=16
# Release binaries rather than Debian's restic, which trails upstream, and supercronic, which
# Debian does not package. Checksums from each release's SHA256SUMS (restic) or computed from
# the release asset (supercronic).
ARG RESTIC_VERSION=0.19.1
ARG RESTIC_SHA256_AMD64=f415415624dcc452f2a02b8c33641791a8c6d6d3b65bbb3543fcf9a25151585c
ARG RESTIC_SHA256_ARM64=a5f64aaab53d51e311fa3829124c5b703f2d14cf187d8640b6be3b2b49376465
ARG SUPERCRONIC_VERSION=0.2.51
ARG SUPERCRONIC_SHA256_AMD64=cc232108374e8e2b537045f2d5eaa1229f619db314c3ba6a14d38b35dc0aa123
ARG SUPERCRONIC_SHA256_ARM64=4ae22576a78223d8ccdf5f3b3660a5cbb5288416bf0ca96afc4a36d195932b94
# amd64 is the mini; arm64 builds it on an Apple-silicon laptop.
ARG TARGETARCH

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends bzip2 ca-certificates curl tzdata; \
    install -d /usr/share/postgresql-common/pgdg; \
    curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
        https://www.postgresql.org/media/keys/ACCC4CF8.asc; \
    . /etc/os-release; \
    echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends "postgresql-client-${PG_MAJOR}"; \
    case "$TARGETARCH" in \
        amd64) restic_sha="$RESTIC_SHA256_AMD64"; supercronic_sha="$SUPERCRONIC_SHA256_AMD64" ;; \
        arm64) restic_sha="$RESTIC_SHA256_ARM64"; supercronic_sha="$SUPERCRONIC_SHA256_ARM64" ;; \
        *) echo "no checksums for $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/restic.bz2 \
        "https://github.com/restic/restic/releases/download/v${RESTIC_VERSION}/restic_${RESTIC_VERSION}_linux_${TARGETARCH}.bz2"; \
    echo "${restic_sha}  /tmp/restic.bz2" | sha256sum -c -; \
    bunzip2 -c /tmp/restic.bz2 > /usr/local/bin/restic; \
    curl -fsSL -o /usr/local/bin/supercronic \
        "https://github.com/aptible/supercronic/releases/download/v${SUPERCRONIC_VERSION}/supercronic-linux-${TARGETARCH}"; \
    echo "${supercronic_sha}  /usr/local/bin/supercronic" | sha256sum -c -; \
    chmod +x /usr/local/bin/restic /usr/local/bin/supercronic; \
    rm /tmp/restic.bz2; \
    apt-get purge -y --auto-remove bzip2 curl; \
    rm -rf /var/lib/apt/lists/*

# restic keeps a local copy of the repository's index, so a run fetches only what changed from
# B2. compose.yaml puts it on a named volume; the directory exists here so the volume starts
# out owned by the image's user (uid 1000, as in the app image; Debian already has a `backup`).
ENV RESTIC_CACHE_DIR=/var/cache/restic
RUN useradd --create-home --uid 1000 argelander \
    && install -d -o argelander -g argelander "$RESTIC_CACHE_DIR"

COPY entrypoint.sh /usr/local/bin/entrypoint
COPY backup.sh /usr/local/bin/argelander-backup
COPY crontab /etc/argelander-backup.crontab

USER argelander
ENTRYPOINT ["entrypoint"]
# JSON log lines, like the other services.
CMD ["supercronic", "-json", "/etc/argelander-backup.crontab"]

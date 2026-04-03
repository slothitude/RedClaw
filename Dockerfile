FROM python:3.13-slim AS builder

WORKDIR /build
COPY pyproject.toml README.md ./
COPY redclaw/ redclaw/

RUN pip install --no-cache-dir .

# ---- runtime ----
FROM python:3.13-slim

LABEL org.opencontainers.image.title="RedClaw"
LABEL org.opencontainers.image.description="Minimal AI coding agent"
LABEL org.opencontainers.image.source="https://github.com/slothitude/RedClaw"

RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=builder /usr/local/bin/redclaw /usr/local/bin/redclaw
COPY redclaw/ /opt/redclaw/redclaw/

# Config and data volumes
VOLUME ["/root/.redclaw", "/workspace"]

WORKDIR /workspace

EXPOSE 8080 9090

ENTRYPOINT ["redclaw"]
CMD ["--help"]

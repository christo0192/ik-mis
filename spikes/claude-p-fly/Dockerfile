# claude -p on Fly.io -- de-risk spike (PLAN.md Phase 0 / Step 4).
# Minimal image: does ONE thing -- prove `claude -p` (or the ANTHROPIC_API_KEY
# fallback) can complete a headless call unattended on a Fly machine.
#
# Pattern cloned from the proven /mnt/c/mcp-servers/anomaly-engine/fly-deploy/
# Dockerfile (Node 20 + @anthropic-ai/claude-code + non-root user), stripped of
# everything specific to the anomaly pipeline (no Playwright/Chromium, no
# pipeline source, no Sheets/BQ/Slack creds).
#
# Deploy from this directory:
#   fly deploy . --config fly.toml --dockerfile Dockerfile

FROM python:3.12-slim

# OS deps: ca-certificates (HTTPS/npm/anthropic API), curl (node setup), tzdata.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl tzdata gnupg \
    && rm -rf /var/lib/apt/lists/*

# Node 20 for the Claude Code CLI (documented install method, mirrors the
# anomaly-engine fly-deploy/Dockerfile which is the proven pattern in this repo).
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g @anthropic-ai/claude-code

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/home/appuser

WORKDIR /app

# Non-root user: the Claude CLI refuses to run with --dangerously-skip-permissions
# as root (same reason the anomaly-engine image runs as appuser). The spike
# doesn't pass that flag, but we keep the non-root pattern since it's what the
# real ik-ad-generator / weekly-pipeline deploys will need anyway.
RUN useradd -m -u 10001 -s /bin/bash appuser

COPY spike_test.py /app/spike_test.py
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh \
    && chown -R appuser:appuser /app /home/appuser

USER appuser

ENTRYPOINT ["/app/entrypoint.sh"]

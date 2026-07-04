# Fly.io image -- ik-ad-generator (PLAN.md Phase 1 Step 14).
# Scheduled batch container for the Generation department's daily pipeline
# chain (see run_daily.sh): collect_kw_perf -> lead_quality -> generate_assets
# -> gate_keywords -> export_negatives -> instrument -> notify_slack.
#
# Pattern cloned from the proven /mnt/c/mcp-servers/anomaly-engine/fly-deploy/
# Dockerfile (Node 20 + @anthropic-ai/claude-code CLI + non-root user,
# restart=never) and spikes/claude-p-fly/Dockerfile (the de-risk spike that
# verified `claude -p` works unattended on Fly via CLAUDE_CODE_OAUTH_TOKEN).
# No Playwright/Chromium here (unlike anomaly-engine) -- this pipeline never
# renders screenshots.
#
# Build context = THIS REPO ROOT (the MIS repo), unlike anomaly-engine which
# builds from its shared C:\mcp-servers root -- see
# fly-deploy/vendor/slack-tools/slack_post.py's own docstring for why.
#
# Deploy from the MIS repo root:
#   fly deploy . --config fly.toml --dockerfile fly-deploy/Dockerfile

FROM python:3.12-slim

# OS deps: ca-certificates (HTTPS/npm/Anthropic API/BigQuery/Google Ads),
# curl (node setup), tzdata (IST).
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl tzdata gnupg \
    && rm -rf /var/lib/apt/lists/*

# Node 20 for the Claude Code CLI (generation/llm.py shells out to `claude -p`
# for the synth/extract model calls) -- same install method as the proven
# anomaly-engine/fly-deploy/Dockerfile and spikes/claude-p-fly/Dockerfile.
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g @anthropic-ai/claude-code

ENV TZ=Asia/Kolkata \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    IK_MCP_DIR=/app \
    HOME=/home/appuser

WORKDIR /app

# Non-root user: the Claude CLI refuses to run with --dangerously-skip-permissions
# as root (same constraint the anomaly-engine image documents).
RUN useradd -m -u 10001 -s /bin/bash appuser

# Python deps first (cache-friendly). No venv in the image -- pip installs
# straight into the system interpreter, matching run_daily.sh's default
# GENERATION_PYTHON_BIN=python3.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /app/requirements.txt

# Source: the Generation department's own code.
COPY generation/ /app/generation/
COPY run_daily.sh /app/run_daily.sh

# Vendored Slack helper -- see fly-deploy/vendor/slack-tools/slack_post.py's
# docstring for why this is a copy rather than a shared build context.
COPY fly-deploy/vendor/slack-tools/ /app/slack-tools/

# Fly orchestration.
COPY fly-deploy/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh /app/run_daily.sh \
    && rm -rf /app/generation/__pycache__ /app/generation/staging/* \
    && chown -R appuser:appuser /app /home/appuser

# Run the whole pipeline as non-root (required for the Claude CLI).
USER appuser

ENTRYPOINT ["/app/entrypoint.sh"]

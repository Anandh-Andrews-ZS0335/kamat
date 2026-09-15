# ==============================================================================
# Last Mile — Production Container Dockerfile
# Multi-stage optimized build for Cloud & On-Premises deployments
# ==============================================================================
FROM python:3.11-slim AS base

# Install system utilities & COIN-OR CBC MIP solver binary for PuLP
RUN apt-get update && apt-get install -y --no-install-recommends \
    coinor-cbc \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install uv package manager from official image
COPY --from=ghcr.io/astral-sh/uv:0.6.5 /uv /uvx /bin/

WORKDIR /app

# Configure environment
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_SYSTEM_PYTHON=0 \
    PATH="/app/.venv/bin:$PATH" \
    PORT=8000 \
    BANK_API_PORT=8001 \
    BANK_API_URL=http://127.0.0.1:8001 \
    LASTMILE_DATA_DIR=/app/data/engine \
    BANK_DATA_DIR=/app/data/bank

# Copy dependency specifications
COPY pyproject.toml uv.lock ./

# Install dependencies using uv into virtual environment
RUN uv sync --frozen --no-install-project --no-dev

# Copy application source & configurations
COPY src/ ./src/
COPY config/ ./config/
COPY scripts/ ./scripts/
COPY README.md ./

# Install the project packages
RUN uv sync --frozen --no-dev

# Pre-seed initial synthetic credit union data
RUN python -m bank_api seed

# Create volume mount points for persistent database storage
VOLUME ["/app/data"]

EXPOSE 8000

# Run production multi-process supervisor
CMD ["python", "scripts/start_prod.py"]

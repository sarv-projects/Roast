# ── Stage 1: Build frontend ───────────────────────────────────────────────────
FROM node:20-slim AS frontend-builder

WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --silent
COPY frontend/ ./
RUN npm run build
# Output: /app/frontend/dist


# ── Stage 2: Python app ───────────────────────────────────────────────────────
FROM python:3.12-slim

# System deps for PyMuPDF
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmupdf \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN pip install uv --no-cache-dir

WORKDIR /app

# Install Python deps via uv (uses uv.lock for reproducible builds)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Copy application code
COPY backend/ ./backend/
COPY ingestion/ ./ingestion/
COPY scripts/ ./scripts/

# Copy SQLite corpus DB (pre-populated market signals)
COPY ingestion/market_intel.db ./ingestion/market_intel.db

# Copy built frontend from stage 1
COPY --from=frontend-builder /app/frontend/dist ./frontend/dist

# Expose port
EXPOSE 8080

# Run with uv's managed Python
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -f http://localhost:8080/health || exit 1
CMD ["uv", "run", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8080"]

# Docker configuration for auto-bedrock-chat-fastapi
# Multi-stage build for optimal image size
FROM python:3.11-slim AS builder

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential=12.9 \
    curl=7.88.1-10+deb12u14 \
    ca-certificates=20230311+deb12u1 \
    && apt-mark auto build-essential curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy source code and install in development mode
COPY . /tmp/src/
WORKDIR /tmp/src
RUN pip install .

# Production stage
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl=7.88.1-10+deb12u14 \
    ca-certificates=20230311+deb12u1 \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Create non-root user
RUN groupadd -r appuser && useradd -r -g appuser appuser

# Copy virtual environment from builder stage
COPY --from=builder /opt/venv /opt/venv

# Create app directory
WORKDIR /app

# Copy application code
COPY . .

# Change ownership to appuser
RUN chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Default command - equivalent to: poetry run uvicorn auto_bedrock_chat_fastapi.app:app --reload --host 0.0.0.0
CMD ["uvicorn", "auto_bedrock_chat_fastapi.app:app", "--host", "0.0.0.0", "--port", "8000"]
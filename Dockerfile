# Notification Service - Production Dockerfile
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY notification-service/requirements.txt .

# Install Python dependencies and fail fast if critical runtime deps are missing
RUN pip install --no-cache-dir -r requirements.txt && \
    python -c "import requests, httpx; print('deps-ok')"

# Copy shared module (required for middleware imports)
COPY shared/ shared/

# Copy application
COPY notification-service/src/ src/

# Create non-root user with proper directory permissions
RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app && \
    chmod 775 /app

USER appuser

# Expose port
EXPOSE 8012

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8012/health', timeout=2.0)" || exit 1

# Run application
CMD ["python", "-m", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8012"]

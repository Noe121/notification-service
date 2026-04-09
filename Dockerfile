# Notification Service - Production Dockerfile
# ---------- builder stage ----------
FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY notification-service/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---------- runtime stage ----------
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Download Amazon RDS CA bundle for full SSL certificate verification
RUN curl -sS -o /etc/ssl/certs/global-bundle.pem https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem

COPY --from=builder /install /usr/local

# Verify critical deps
RUN python -c "import requests, httpx; print('deps-ok')"

RUN useradd -m -u 1000 appuser
WORKDIR /app

COPY --chown=appuser:appuser shared/ shared/
COPY --chown=appuser:appuser notification-service/src/ src/

USER appuser

EXPOSE 8012

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -sf http://localhost:8012/health || exit 1

ENV DB_SSL_CA_PATH=/etc/ssl/certs/global-bundle.pem

CMD ["python", "-m", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8012"]

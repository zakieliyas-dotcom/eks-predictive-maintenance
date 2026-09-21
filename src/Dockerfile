# src/Dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY predictive_maintenance/ ./predictive_maintenance/

# Runs as non-root - no reason this workload needs root, and it's an
# easy, free hardening step for a pod with an IAM role attached.
RUN useradd -m appuser
USER appuser

ENTRYPOINT ["python", "-m", "predictive_maintenance.service"]

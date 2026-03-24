FROM python:3.12-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl docker.io \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY pyproject.toml .
RUN pip install --no-cache-dir -e . && \
    pip install --no-cache-dir sentence-transformers

# Copy source
COPY . .

# Create data dirs
RUN mkdir -p agent/memory agent/tasks agent/finance agent/vault agent/logs

CMD ["python", "-m", "agent"]

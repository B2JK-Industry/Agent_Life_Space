FROM python:3.12-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl docker.io \
    && rm -rf /var/lib/apt/lists/*

# Copy source first, then install (non-editable)
COPY pyproject.toml .
COPY agent/ agent/
COPY README.md .
RUN pip install --no-cache-dir . && \
    pip install --no-cache-dir sentence-transformers

# Copy remaining files (docs, tests, config)
COPY . .

# Create data dirs
RUN mkdir -p agent/memory agent/tasks agent/finance agent/vault agent/logs

CMD ["python", "-m", "agent"]

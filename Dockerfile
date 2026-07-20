FROM python:3.13-slim AS base

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        gh \
        fossil \
        ripgrep \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies (installed separately for layer caching)
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY . .
RUN pip install --no-cache-dir -e .

# Create data directory
RUN mkdir -p /app/data

# Copy entrypoint script
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/app/docker-entrypoint.sh"]

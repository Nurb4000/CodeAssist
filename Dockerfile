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

# Never ship a baked-in SQLite DB (schema/WAL files copy sneakier than
# .dockerignore patterns). The image must start with a clean data dir; at
# runtime /app/data is the persistence volume.
RUN rm -rf /app/codeassist/data && mkdir -p /app/data

# Copy entrypoint script
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

EXPOSE 8090

ENTRYPOINT ["/app/docker-entrypoint.sh"]

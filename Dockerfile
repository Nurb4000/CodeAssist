FROM python:3.13-slim AS base

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
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

EXPOSE 8000

ENTRYPOINT ["python", "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]

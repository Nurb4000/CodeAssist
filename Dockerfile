FROM python:3.13-slim AS base

# System dependencies. Minimal, non-recommends set for the app itself. libatomic1
# is required by the Playwright driver's bundled Node before `playwright install
# --with-deps` can run (notably for Firefox/WebKit). nodejs is required by the
# D1 Code Mode `execute` tool (confined Node.js vm runtime). The ssh/scp/sftp,
# sshpass and rsync group supports handing code tests off to a dedicated VM;
# nc/ping/dig check reachability and jq parses results from remote test APIs.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        gh \
        fossil \
        ripgrep \
        ca-certificates \
        curl \
        sshpass \
        openssh-client \
        rsync \
        netcat-openbsd \
        iputils-ping \
        dnsutils \
        jq \
        libatomic1 \
        nodejs \
    && rm -rf /var/lib/apt/lists/*

# Chromium powers the screenshot tool (headless Chrome via CDP). Installed with
# recommends so every shared lib headless capture needs is present; the tool
# launches it with --no-sandbox --disable-dev-shm-usage for root-in-container.
RUN apt-get update && apt-get install -y \
        chromium \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies (installed separately for layer caching). All three
# Playwright engines are installed into a dedicated layer so app-code rebuilds
# do not re-download them; the browser tool reads PLAYWRIGHT_BROWSERS_PATH and
# selects one via [browser] engine = "chromium" | "firefox" | "webkit".
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install --with-deps chromium firefox webkit \
    && rm -rf /var/lib/apt/lists/*

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

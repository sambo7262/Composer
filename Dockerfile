# Stage 1: Base with Python dependencies
FROM python:3.12-slim AS base

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies (cached layer)
WORKDIR /app
COPY requirements.txt .
ARG TARGETARCH
RUN pip install --no-cache-dir -r requirements.txt && \
    if [ "$TARGETARCH" = "amd64" ]; then \
      pip install --no-cache-dir "essentia>=2.1b6.dev1389"; \
    else \
      echo "Skipping essentia on $TARGETARCH (no wheel available — audio analysis disabled)"; \
    fi

# Stage 2: Build Tailwind CSS
FROM base AS css-builder

# Download Tailwind CSS standalone CLI (detect arch for multi-platform builds)
ARG TARGETARCH
RUN if [ "$TARGETARCH" = "amd64" ]; then TWARCH=x64; else TWARCH=$TARGETARCH; fi && \
    curl -sL -o /usr/local/bin/tailwindcss \
    "https://github.com/tailwindlabs/tailwindcss/releases/latest/download/tailwindcss-linux-${TWARCH}" && \
    chmod +x /usr/local/bin/tailwindcss

COPY . .
RUN tailwindcss -i ./app/static/css/input.css -o ./app/static/css/output.css --minify

# Stage 3: Final image
FROM base AS final

COPY . /app/
COPY --from=css-builder /app/app/static/css/output.css /app/app/static/css/output.css

# Create data directory for SQLite + encryption key
RUN mkdir -p /app/data

VOLUME /app/data
EXPOSE 8085

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8085/api/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8085"]

from __future__ import annotations

import os

# Data directory - configurable via environment variable
# Docker: /app/data (default), Local dev: ./data
DATA_DIR = os.environ.get("COMPOSER_DATA_DIR", os.environ.get("_COMPOSER_LOCAL_DEV", "./data"))

# Ensure data directory exists
os.makedirs(DATA_DIR, exist_ok=True)

# Database URL constructed from DATA_DIR
DATABASE_URL = f"sqlite:///{DATA_DIR}/composer.db"

# Encryption key file path
ENCRYPTION_KEY_PATH = os.path.join(DATA_DIR, ".encryption.key")

# Application port
APP_PORT = int(os.environ.get("COMPOSER_PORT", "8085"))

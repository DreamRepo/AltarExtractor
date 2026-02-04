"""
Configuration settings for AltarExtractor.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file from project root (override=True to replace system env vars)
env_path = Path(__file__).resolve().parent.parent / ".env"
loaded = load_dotenv(env_path, override=True)
print(f"[CONFIG] .env loaded from: {env_path} (found: {loaded})")

# MongoDB connection settings (from .env) - using ALTAR_ prefix to avoid conflicts
MONGO_HOST = os.environ.get("ALTAR_MONGO_HOST", "localhost")
MONGO_PORT = int(os.environ.get("ALTAR_MONGO_PORT", "27017"))
MONGO_USERNAME = os.environ.get("ALTAR_MONGO_USERNAME", "")
MONGO_PASSWORD = os.environ.get("ALTAR_MONGO_PASSWORD", "")
# Auth source: "admin", "auto" (use database name), or specific database name
MONGO_AUTH_SOURCE = os.environ.get("ALTAR_MONGO_AUTH_SOURCE", "admin")

# Allowed databases (comma-separated list, empty = all)
_allowed_dbs = os.environ.get("ALTAR_ALLOWED_DATABASES", "")
ALLOWED_DATABASES = [db.strip() for db in _allowed_dbs.split(",") if db.strip()] if _allowed_dbs else []

# Default database name
DEFAULT_DB_NAME = os.environ.get("ALTAR_DEFAULT_DB", "sacred")

# Debug mode
DEBUG = os.environ.get("ALTAR_DEBUG", "false").lower() in ("true", "1", "yes")

# Debug: print loaded configuration
print(f"[CONFIG] MONGO_HOST: {MONGO_HOST}")
print(f"[CONFIG] MONGO_PORT: {MONGO_PORT}")
print(f"[CONFIG] MONGO_USERNAME: {'***' if MONGO_USERNAME else '(empty)'}")
print(f"[CONFIG] MONGO_AUTH_SOURCE: {MONGO_AUTH_SOURCE}")
print(f"[CONFIG] ALLOWED_DATABASES: {ALLOWED_DATABASES}")

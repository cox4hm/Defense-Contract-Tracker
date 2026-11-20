"""Settings read from environment variables (see .env.example)."""
import os

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://defense:defense@localhost:5432/defense_tracker"
)
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
USASPENDING_BASE = os.environ.get("USASPENDING_BASE", "https://api.usaspending.gov")
REQUEST_PAUSE_SECONDS = float(os.environ.get("REQUEST_PAUSE_SECONDS", "0.6"))
SCHEDULE_AT = os.environ.get("SCHEDULE_AT", "06:40")  # used by the Docker scheduler
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
PORTFOLIO_URL = os.environ.get("PORTFOLIO_URL", "")
SOURCE_URL = os.environ.get("SOURCE_URL", "")      # e.g. https://github.com/you/defense-contract-tracker

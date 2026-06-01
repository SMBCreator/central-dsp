"""Central DSP — application settings."""
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # ── Database ───────────────────────────────────────────────────────
    DATABASE_URL: str = "postgresql://centraldsp:centraldsp@postgres:5432/centraldsp"

    # ── Redis / Celery ────────────────────────────────────────────────
    REDIS_URL: str = "redis://redis:6379/0"
    CELERY_BROKER_URL: str = "redis://redis:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://redis:6379/2"

    # ── Auth (JWT) ────────────────────────────────────────────────────
    SECRET_KEY: str = "change-me-in-production-use-256bit-random"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # ── First admin (created on startup if DB is empty) ──────────────────
    FIRST_ADMIN_EMAIL: str = "admin@centraldsp.local"
    FIRST_ADMIN_PASSWORD: str = "admin123"

    # ── Base URL (used in nurl/lurl macros + SSP test) ────────────────
    # In production: https://dsp.yourdomain.com
    # In docker-compose: http://backend:8000
    BACKEND_BASE_URL: str = "http://backend:8000"
    FRONTEND_URL: str = "http://localhost:3000"

    # ── RTB / Bidding settings ─────────────────────────────────────────
    RTB_DEFAULT_BID_FLOOR: float = 0.01       # USD CPM
    RTB_MAX_BID_PRICE: float = 50.0           # USD CPM hard cap
    RTB_BID_TIMEOUT_MS: int = 100             # max response time to SSP
    RTB_CAMPAIGN_CACHE_TTL: int = 30          # seconds to cache campaign list
    RTB_CURRENCY: str = "USD"                 # default billing currency
    RTB_ENABLE_RUB: bool = True               # accept RUB bids (convert to USD)
    USD_RUB_RATE: float = 90.0                # fallback rate if no live rate

    # ── GeoIP (MaxMind GeoLite2) ───────────────────────────────────────
    GEOIP_DB_PATH: str = "/geoip/GeoLite2-City.mmdb"

    # ── Tracking ──────────────────────────────────────────────────────
    CLICK_TTL_DAYS: int = 30
    POSTBACK_DEDUP_WINDOW: int = 3600  # seconds

    # ── Misc ───────────────────────────────────────────────────────────
    LOG_LEVEL: str = "INFO"
    ENVIRONMENT: str = "development"  # development|staging|production

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()

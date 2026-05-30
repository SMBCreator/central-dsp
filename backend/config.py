from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "postgresql://centraldsp:password@postgres:5432/centraldsp"

    # Redis
    REDIS_URL: str = "redis://redis:6379/0"

    # JWT
    JWT_SECRET_KEY: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440  # 24h
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # Encryption for ad network credentials
    ENCRYPTION_KEY: str = "change-me-32-byte-key-here------"  # must be 32 chars

    # First admin (auto-created if users table empty)
    FIRST_ADMIN_EMAIL: str = "admin@centraldsp.local"
    FIRST_ADMIN_PASSWORD: str = "admin123"

    # RTB Bidder
    RTB_BIDDER_HOST: str = "bidder"
    RTB_BIDDER_PORT: int = 8080
    RTB_DEFAULT_BID_FLOOR: float = 0.01
    RTB_MAX_BID_PRICE: float = 5.00
    RTB_RESPONSE_TIMEOUT_MS: int = 100

    # Notifications (Apprise)
    TELEGRAM_BOT_TOKEN: Optional[str] = None
    TELEGRAM_CHAT_ID: Optional[str] = None
    SMTP_HOST: Optional[str] = None
    SMTP_PORT: int = 587
    SMTP_USER: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None

    # Ad Networks
    PUSH_HOUSE_API_TOKEN: Optional[str] = None

    # Environment
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    LOG_LEVEL: str = "info"

    class Config:
        env_file = ".env"
        extra = "allow"


settings = Settings()

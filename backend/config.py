from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql://centraldsp:centraldsp@postgres:5432/centraldsp"

    # Redis
    redis_url: str = "redis://redis:6379/0"

    # JWT
    jwt_secret_key: str = "change_me_in_production_256bit_secret"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 1440  # 24h
    jwt_refresh_token_expire_days: int = 30

    # Encryption key for ad network credentials (32 bytes base64)
    encryption_key: str = "Y2hhbmdlX21lX2luX3Byb2R1Y3Rpb24zMg=="

    # First admin (auto-created if users table empty)
    first_admin_email: str = "admin@centraldsp.local"
    first_admin_password: str = "admin123"

    # RTB4FREE Bidder
    rtb_bidder_host: str = "bidder"
    rtb_bidder_port: int = 8080
    rtb_default_bid_floor: float = 0.01
    rtb_max_bid_price: float = 5.00
    rtb_response_timeout_ms: int = 100

    # Notifications (Apprise)
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Environment
    environment: str = "development"
    debug: bool = True
    log_level: str = "info"

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

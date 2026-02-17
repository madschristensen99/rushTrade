from pydantic_settings import BaseSettings
from functools import lru_cache
from pydantic import ConfigDict

class Settings(BaseSettings):
    model_config = ConfigDict(extra='ignore', env_file=".env", case_sensitive=True)
    # App
    APP_NAME: str = "Trading Terminal Backend"
    DEBUG: bool = False
    ENVIRONMENT: str = "development"  # "development" or "production"
    API_V1_PREFIX: str = "/api/v1"
    
    # Security
    SECRET_KEY: str 
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    
    # Database
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str
    
    @property
    def database_url(self) -> str:
        return f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
    
    @property
    def DATABASE_URL(self) -> str:
        return self.database_url
    
    # Redis
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: str | None = None
    
    @property
    def redis_url(self) -> str:
        if self.REDIS_PASSWORD:
            return f"redis://:{self.REDIS_PASSWORD}@{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
    
    # Celery
    CELERY_BROKER_URL: str = "amqp://guest:guest@localhost:5672//"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/0"
    
    @property
    def celery_broker(self) -> str:
        return self.CELERY_BROKER_URL
    
    @property
    def celery_backend(self) -> str:
        return self.CELERY_RESULT_BACKEND or self.redis_url
    
    # Kalshi API
    KALSHI_API_BASE: str = "https://trading-api.kalshi.com/trade-api/v2"
    KALSHI_API_KEY: str = ""
    KALSHI_API_SECRET: str = ""
    KALSHI_USER_ID: str = ""
    
    # Rate Limiting
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_PER_MINUTE: int = 60
    
    # CORS
    CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:5173", "http://127.0.0.1:5173"]
    
    # Stripe
    STRIPE_SECRET_KEY: str | None = None
    STRIPE_WEBHOOK_SECRET: str | None = None
    
    # Telegram
    TELEGRAM_BOT_TOKEN: str | None = None

@lru_cache()
def get_settings() -> Settings:
    return Settings()
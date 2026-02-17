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
    DATABASE_URL: str | None = None  # Direct database URL override
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "rushtrade"
    
    @property
    def database_url(self) -> str:
        # Use DATABASE_URL if set, otherwise build from POSTGRES_ vars
        if self.DATABASE_URL:
            return self.DATABASE_URL
        return f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
    
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
    
    # ── Monad / Chain ─────────────────────────────────────────────────────────
    MONAD_RPC_URL: str = "https://testnet-rpc.monad.xyz"
    MONAD_CHAIN_ID: int = 41454          # Monad testnet; change to mainnet ID when live

    # Deployed contract addresses (populated after `forge script Deploy.s.sol`)
    CTF_ADDRESS: str = ""                # ConditionalTokens.sol
    MARKET_FACTORY_ADDRESS: str = ""     # MarketFactory.sol
    CTF_EXCHANGE_ADDRESS: str = ""       # CTFExchange.sol
    COLLATERAL_TOKEN_ADDRESS: str = ""   # USDC or other whitelisted collateral

    # Operator key – the backend EOA that calls fillOrders() on-chain
    OPERATOR_PRIVATE_KEY: str = ""

    # ── Rate Limiting ──────────────────────────────────────────────────────────
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_PER_MINUTE: int = 60
    
    # CORS - Allow all localhost origins for development
    CORS_ORIGINS: list[str] = ["*"]  # Allow all origins in development
    
    # Stripe
    STRIPE_SECRET_KEY: str | None = None
    STRIPE_WEBHOOK_SECRET: str | None = None
    
    # Telegram
    TELEGRAM_BOT_TOKEN: str | None = None

@lru_cache()
def get_settings() -> Settings:
    return Settings()
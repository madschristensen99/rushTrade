import sys
from pathlib import Path
from loguru import logger
from app.config import get_settings


def setup_logging():
    """Configure loguru logging with rotation and structured output."""
    settings = get_settings()
    
    # Remove default handler
    logger.remove()
    
    # Console handler - colored and formatted
    logger.add(
        sys.stdout,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{extra[request_id]}</cyan> | <level>{message}</level>",
        level="DEBUG" if settings.ENVIRONMENT == "development" else "INFO",
        colorize=True,
    )
    
    # Create logs directory if it doesn't exist
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    
    # Error log - rotating by size
    logger.add(
        log_dir / "error.log",
        rotation="10 MB",
        retention="30 days",
        level="ERROR",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {extra[request_id]} | {name}:{function}:{line} | {message}",
        backtrace=True,
        diagnose=settings.ENVIRONMENT == "development",
    )
    
    # Request log - captures all HTTP requests with user info
    logger.add(
        log_dir / "requests.log",
        rotation="50 MB",
        retention="60 days",
        level="INFO",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {extra[request_id]} | {extra[user_id]} | {extra[method]} {extra[path]} | {extra[status_code]} | {extra[duration_ms]}ms | {message}",
        filter=lambda record: "request_id" in record["extra"],
    )
    
    # Production JSON logs
    if settings.ENVIRONMENT == "production":
        logger.add(
            log_dir / "app.json",
            rotation="100 MB",
            retention="90 days",
            serialize=True,
            level="INFO",
        )
    
    # Set default context
    logger.configure(extra={"request_id": "-", "user_id": "-", "method": "-", "path": "-", "status_code": "-", "duration_ms": "-"})
    
    logger.info(f"Logging initialized - Environment: {settings.ENVIRONMENT}")
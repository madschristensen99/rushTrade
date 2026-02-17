import traceback
from typing import Any, Dict
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import SQLAlchemyError
from loguru import logger
from app.config import get_settings

settings = get_settings()


class AppException(Exception):
    """Base application exception."""
    def __init__(self, message: str, status_code: int = 500):
        self.message = message
        self.status_code = status_code
        super().__init__(self.message)


class NotFoundError(AppException):
    """Resource not found exception."""
    def __init__(self, message: str = "Resource not found"):
        super().__init__(message, status_code=404)


class UnauthorizedError(AppException):
    """Unauthorized access exception."""
    def __init__(self, message: str = "Unauthorized"):
        super().__init__(message, status_code=401)


class ForbiddenError(AppException):
    """Forbidden access exception."""
    def __init__(self, message: str = "Forbidden"):
        super().__init__(message, status_code=403)


class BadRequestError(AppException):
    """Bad request exception."""
    def __init__(self, message: str = "Bad request"):
        super().__init__(message, status_code=400)


class RateLimitError(AppException):
    """Rate limit exceeded exception."""
    def __init__(self, message: str = "Rate limit exceeded"):
        super().__init__(message, status_code=429)


class KalshiAPIError(AppException):
    """Kalshi API error exception."""
    def __init__(self, message: str = "Kalshi API error"):
        super().__init__(message, status_code=502)


def get_request_id(request: Request) -> str:
    """Extract or generate request ID for tracking"""
    return request.headers.get("X-Request-ID", "unknown")


def get_user_id(request: Request) -> str:
    """Extract user ID from request state"""
    user = getattr(request.state, "user", None)
    if user:
        return str(getattr(user, "id", "anonymous"))
    return "anonymous"


def sanitize_validation_errors(errors: list) -> Dict[str, Any]:
    """Sanitize validation errors for production"""
    if settings.ENVIRONMENT == "production":
        return {
            "errors": [
                {
                    "field": ".".join(str(loc) for loc in err.get("loc", [])),
                    "message": err.get("msg", "Invalid value")
                }
                for err in errors
            ]
        }
    return {"errors": errors}


def setup_exception_handlers(app: FastAPI) -> None:
    """Setup custom exception handlers for the FastAPI app."""
    
    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException):
        request_id = get_request_id(request)
        user_id = get_user_id(request)
        
        logger.bind(
            request_id=request_id,
            user_id=user_id,
            path=request.url.path,
            status_code=exc.status_code,
            exception_type=exc.__class__.__name__
        ).warning(f"AppException: {exc.__class__.__name__} - {exc.message}")
        
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": exc.message,
                "type": exc.__class__.__name__,
                "request_id": request_id
            }
        )
    
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        request_id = get_request_id(request)
        user_id = get_user_id(request)
        
        logger.bind(
            request_id=request_id,
            user_id=user_id,
            errors=exc.errors()
        ).warning(f"Validation error on {request.url.path}")
        
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "detail": sanitize_validation_errors(exc.errors()),
                "type": "ValidationError",
                "request_id": request_id
            }
        )
    
    @app.exception_handler(SQLAlchemyError)
    async def sqlalchemy_exception_handler(request: Request, exc: SQLAlchemyError):
        request_id = get_request_id(request)
        user_id = get_user_id(request)
        
        logger.bind(
            request_id=request_id,
            user_id=user_id,
            path=request.url.path,
            error_type=exc.__class__.__name__
        ).error(f"Database error: {str(exc)}")
        
        detail = "Database error occurred" if settings.ENVIRONMENT == "production" else f"Database error: {str(exc)}"
        
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "detail": detail,
                "type": "DatabaseError",
                "request_id": request_id
            }
        )
    
    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception):
        request_id = get_request_id(request)
        user_id = get_user_id(request)
        
        logger.bind(
            request_id=request_id,
            user_id=user_id,
            path=request.url.path,
            method=request.method,
            exception_type=exc.__class__.__name__,
            traceback=traceback.format_exc()
        ).error(f"Unhandled exception: {exc.__class__.__name__}: {str(exc)}")
        
        detail = "Internal server error" if settings.ENVIRONMENT == "production" else f"{exc.__class__.__name__}: {str(exc)}"
        
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "detail": detail,
                "type": "InternalError",
                "request_id": request_id
            }
        )
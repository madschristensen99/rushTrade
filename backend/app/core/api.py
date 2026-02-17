from fastapi import APIRouter
from app.modules.user.urls import router as user_router
from app.modules.terminal.auto.urls import router as auto_router

api_router = APIRouter()

api_router.include_router(user_router)
api_router.include_router(auto_router, prefix="/terminal")
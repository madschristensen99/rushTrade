from fastapi import APIRouter
from app.modules.user.urls import router as user_router
from app.modules.terminal.clob.urls import router as clob_router

api_router = APIRouter()

api_router.include_router(user_router)
api_router.include_router(clob_router, prefix="/terminal")
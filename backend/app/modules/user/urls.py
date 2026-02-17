from fastapi import APIRouter
from app.modules.user import views

router = APIRouter(prefix="/auth", tags=["Authentication"])

router.include_router(views.router)
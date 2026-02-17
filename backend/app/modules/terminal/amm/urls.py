from fastapi import APIRouter
from app.modules.terminal.auto import views

router = APIRouter(prefix="/auto", tags=["Auto Trading"])
router.include_router(views.router)

from fastapi import APIRouter
from app.modules.terminal.clob.views import router as clob_views_router

router = APIRouter(prefix="/clob", tags=["CLOB"])
router.include_router(clob_views_router)

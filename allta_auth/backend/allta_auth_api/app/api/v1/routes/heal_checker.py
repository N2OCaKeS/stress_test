from fastapi import APIRouter

router = APIRouter(
    prefix="",
    tags=["Служебные"],
)

@router.get("/health")
async def health():
    return {"status": "ok"}

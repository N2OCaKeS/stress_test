from fastapi import APIRouter

router = APIRouter(
    prefix="",
    tags=["Health"],
)

@router.get("/health")
async def health():
    return {"status": "ok"}
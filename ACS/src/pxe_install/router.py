from fastapi import APIRouter, Depends

from src.pxe_install.server_presets import ServerPresets

router = APIRouter(
    prefix="/pxe-install",
    tags=["PXE install"]
)

@router.get("/server-presets")
def presets_server():
    sp = ServerPresets()
    sp.tune()
    return {"Сервер настроен"}

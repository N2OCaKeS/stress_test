from fastapi import APIRouter, Depends
from src.add_tuning.router import get_info_stand
from src.pxe_install.server_presets import ServerPresets
from src.pxe_install.prep_install_os import PreparingForInstallationOS

router = APIRouter(
    prefix="/pxe-install",
    tags=["PXE install"]
)

@router.get("/server-presets")
def presets_server():
    sp = ServerPresets()
    sp.tune()
    return {"Сервер настроен"}


@router.post("/install-os")
def install_os(astra_build_version: str, uefi: bool = True, stand = Depends(get_info_stand)):
    prep_inst_os = PreparingForInstallationOS(astra_build_version=astra_build_version, stand_name=stand[1])
    prep_inst_os.prepare(uefi=uefi)
    return {"Началась установка по сети"}
    ####
    # TODO Выставить PXE загрузку на 1 место и ребут
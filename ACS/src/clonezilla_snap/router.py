from fastapi import APIRouter, Depends
from fabric import Connection
from time import time
from .clonezilla_func import backup_image 

from src.add_tuning.router import get_info_stand
from src.utils.secondary_func import func_filter_version

router = APIRouter(
    prefix="/clonezilla-snap",
    tags=["Clonzilla snapshot"]
)

"""
    TODO Возможно тоже добавить в CELERY!!!
"""
@router.get("/check-snapshots")
def get_snapshots(password_clonezilla_server: str):
    result = Connection("10.177.103.10", user="u", connect_kwargs={"password": f"{password_clonezilla_server}"}).run("ls /home/partimag", hide=True)
    snaps = list(filter(lambda x: x != "nohup.out", result.stdout.strip().split("\n")))
    return {"snaphosts": snaps}


"""
    TODO Добавить в CELERY!!!!!
"""
@router.post("/restore-backup")
def restore_backup(version_name: str, password_clonezilla_server: str, stand = Depends(get_info_stand)):
    version_name_for_clonezilla = func_filter_version(version_name)
    snapshot_name = stand[1] + "-" + version_name_for_clonezilla
    # res = backup_image(stand, snapshot_name, password_clonezilla_server, restore=True)
    backup_image.delay(list(stand), snapshot_name, password_clonezilla_server, restore=True)
    return {f"Отправлено {snapshot_name}"}

"""
    TODO Добавить в CELERY!!!!!
"""
@router.post("/save-disk")
def save_disk(version_name: str, password_clonezilla_server: str, stand = Depends(get_info_stand)):
    version_name_for_clonezilla = func_filter_version(version_name)
    snapshot_name = stand[1] + "-" + version_name_for_clonezilla
    backup_image.delay(list(stand), snapshot_name, password_clonezilla_server, restore=False)
    return {f"Отправлено {snapshot_name}"}

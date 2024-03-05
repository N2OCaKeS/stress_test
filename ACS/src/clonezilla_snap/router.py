from fastapi import APIRouter, Depends
from fabric import Connection
from time import time
from .clonezilla_func import backup_image 

from src.add_tuning.router import get_info_stand


router = APIRouter(
    prefix="/clonezilla-snap",
    tags=["Clonzilla snapshot"]
)

@router.get("/check-snapshots")
def get_snapshots(password_clonezilla_server: str):
    result = Connection("10.177.103.10", user="u", connect_kwargs={"password": f"{password_clonezilla_server}"}).run("ls /home/partimag", hide=True)
    snaps = list(filter(lambda x: x != "nohup.out", result.stdout.strip().split("\n")))
    return {"snaphosts": snaps}

@router.post("/restore-backup")
def restore_backup(version_name: str, password_clonezilla_server: str, stand = Depends(get_info_stand)):
    snapshot_name = stand[1] + "-" + version_name
    print(snapshot_name)
    res = backup_image(stand, snapshot_name, password_clonezilla_server, restore=True)
    print(res, "***")
    return {"Отправлено"}

@router.post("/save-disk")
def save_disk(version_name: str, password_clonezilla_server: str, stand = Depends(get_info_stand)):
    snapshot_name = stand[1] + "-" + version_name
    print(snapshot_name)
    res = backup_image(stand, snapshot_name, password_clonezilla_server, restore=False)
    print(res, "***")
    return {"Отправлено"}
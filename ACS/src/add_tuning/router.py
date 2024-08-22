from time import sleep
from os import remove, listdir, mkdir
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from src.database import get_async_session

from src.utils.secondary_func import remote_cmd, remote_put_file

from src.schemas import Stand
from src.models import stands, versions, repos
from sqlalchemy import select

from .temp import change_repos, astra_version_update, install_kernels

from .conf import (COMPONENTS_INSTALL, 
                   CLONE_GIT_REPO, 
                   COMMAND_WGET_GITCLONE_FILE, 
                   COMMAND_WGET_QAINIT_FILE,
                   NETWORK_SETTINGS_TEMPLATE,
                   COMMON_AUTH_OFF)


router = APIRouter(
    prefix="/add-tuning",
    tags=["Настройка стенда"]
)

@router.get("")
def index():
    pass


async def get_info_stand(stand_name: str, session: AsyncSession = Depends(get_async_session)):
    query = select(stands).where(stands.c.name == stand_name)
    stand = await session.execute(query)
    stand = stand.first()
    return stand

@router.get("/passwd")
def change_pass(current_password: str, stand = Depends(get_info_stand)):
    chngepasswd = 'echo -e "1\n1" | sudo passwd u'
    remote_cmd(chngepasswd, host=stand[3], user=stand[4], passwd=current_password)
    return {"ok"}
    
@router.get("/change-repos")
async def change_repos_r(version_name, stand = Depends(get_info_stand)):
    await change_repos(version_name=version_name, stand=stand)
    return {"ok"}


@router.get("/install-packages/{stand_name}")
def install_packages(stand = Depends(get_info_stand)):
    sleep(20)
    components = " ".join(COMPONENTS_INSTALL)
    command = f"sudo apt update -y && sudo apt install -y {components}"
    print(command)
    data = remote_cmd(command=command, host=stand[3], user=stand[4], passwd=stand[5])
    print("OK 6")
    print(data)
    return {"ok"}
    

@router.get("/clone-git-repo/{stand_name}")
def clone_git_repo(stand = Depends(get_info_stand)):
    data = remote_cmd(command=CLONE_GIT_REPO, host=stand[3], user=stand[4], passwd=stand[5])
    remote_cmd(command=COMMAND_WGET_GITCLONE_FILE, host=stand[3], user=stand[4], passwd=stand[5])
    # print(data)
    return {"ok"}

"""
    TODO  Возможно добавить в CELERY!
"""
@router.get('/wget-qainit')
def wget_qainit(stand = Depends(get_info_stand)):
    remote_cmd(command=COMMAND_WGET_QAINIT_FILE, host=stand[3], user=stand[4], passwd=stand[5])
    return {"ok"}


@router.get("/set-alias")
def set_alias(stand = Depends(get_info_stand)):
    command = "sudo bash qa-init -a"
    remote_cmd(command=command, host=stand[3], user=stand[4], passwd=stand[5])
    return {"ok"}


@router.get("/pamd_auth-off")
def pamd_auth_off(stand=Depends(get_info_stand)):
    remote_cmd(command=COMMON_AUTH_OFF, host=stand[3], user=stand[4], passwd=stand[5])
    return {"ok"}


@router.get("/set-network/{version_name}")
def set_network(version_name: str, stand = Depends(get_info_stand)):
    if version_name.startswith("18"):
        interface = "eno1"
    else:
        interface = "eth0"
    
    net_settings_file = NETWORK_SETTINGS_TEMPLATE.format(interface=interface, ip_address=stand[3])
    if not "network_settings" in listdir():
        mkdir("network_settings")
    with open(f"network_settings/network_{stand[1]}", 'w') as file:
        file.write(net_settings_file)
    remote_put_file(host=stand[3],
                    remote_path=f'/home/u/network_{stand[1]}', 
                    local_path=f"network_settings/network_{stand[1]}",
                    user=stand[4],
                    passwd=stand[5])
    command_mv = f"sudo mv /home/u/network_{stand[1]} /etc/network/interfaces"
    remote_cmd(command=command_mv, host=stand[3], user=stand[4], passwd=stand[5])
    # remote_cmd(command="sudo systemctl restart networking", host=stand[3], user=stand[4], passwd=stand[5])
    remove(f"network_settings/network_{stand[1]}")
    print("OK!!!")
    return {"ok"}


"""
    TODO Возможно добавить в CELERY!
"""
# TODO продумать
@router.get("/install-new-kernel/{version_name}")
def install_kernels(version_name: str, stand = Depends(get_info_stand)):
    # Для 1.7.2 install linux-5.15-generic
    # Для 1.7.3 install linux-5.15-lowlatency
    # Для 1.7.5 install linux-6.1-generic
    return install_kernels(version_name, stand)

"""
    TODO Добавить в CELERY!
"""
@router.get('/astra-update')
def astra_update(new_version, stand = Depends(get_info_stand)):
    astra_version_update.delay(new_version=new_version, stand=list(stand))
    return {"ok"}

"""
    TODO Добавить в CELERY!
"""
@router.get("/all/{version_name}")
async def all_tuning(version_name: str, stand = Depends(get_info_stand), session: AsyncSession = Depends(get_async_session)):
    set_network(version_name=version_name, stand=stand)
    await change_repos(version_name=version_name, stand=stand, session=session)
    install_packages(stand=stand)
    install_kernels(version_name=version_name, stand=stand)
    print("************")
    remote_cmd("sudo reboot", host=stand[3], user=stand[4], passwd=stand[5])
    print("$$$$$$$$")
    sleep(300)
    print("###########")
    clone_git_repo(stand=stand)
    wget_qainit(stand=stand)
    set_alias(stand=stand)
    pamd_auth_off(stand=stand)
    return {"all ok"}

@router.get('/temp')
def temp():
    from .temp import temp_task
    temp_task.delay()
    return {"ВСЕ ОК!"}



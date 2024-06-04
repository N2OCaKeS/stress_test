from time import time, sleep
from src.utils.secondary_func import remote_cmd, socket_available
from src.clonezilla_snap.conf import RESTORE_DISK_COMMAND, SAVE_DISK_COMMAND
from src.tasks.tasks import celery

@celery.task
def backup_image(stand, snap_name: str, password_cs: str, restore=True, *args, **kwargs):
    # print("test")
    start_time = time()
    # result = Connection("10.177.103.10", user="u", connect_kwargs={"password": "team13"}).run(command, hide=True)
    if restore:
        command = RESTORE_DISK_COMMAND.format(ip_address=stand[3], snapshot_name=snap_name, stand_disk=stand[2])
    else:
        command = SAVE_DISK_COMMAND.format(ip_address=stand[3], snapshot_name=snap_name, stand_disk=stand[2])
    print(command)
    result = remote_cmd(command=command, host="10.177.103.10", user="u", passwd=password_cs, read=False)
    print(result)
    print(time() - start_time)
    sleep(15)
    remote_cmd(command="sudo reboot", host=stand[3], user=stand[4], passwd=stand[5])
    sleep(15)
    socket_available(stand_ip=stand[3], user=stand[4], passwd=stand[5])
    sleep(15)
    # return result

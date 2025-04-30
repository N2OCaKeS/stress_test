from allta import VBox, VBoxManager
from ovpn_conf import SYS_VERSION, SYS_VERSION_MOD, SYS_KERNEL, VMS_DATES, VMS_GROUPS, VERSION_OS, VMS, BOX_VERSIONS, OVPN_PATH

VBox.prepare('./prepare_t.sh')
print("PREPARE DONE!!!")
VBox.build(path_to_vagrantfile=OVPN_PATH, box=BOX_VERSIONS[0][0], rc="1.7.5", vms=VMS, vms_dates=VMS_DATES)


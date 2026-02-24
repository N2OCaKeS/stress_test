from allta import SystemCommands, LibvirtManager
from pathlib import Path
VERSION_OS = SystemCommands.check_output_command("cat /etc/astra_version")
KERNEL = SystemCommands.check_output_command("uname -r")
path = Path("vms_dates.txt")
if path.is_file():
    
    VMS_DATES = LibvirtManager.Vm.load_vms_data("vms_dates.txt")
else:
    VMS_DATES = {
        "testvm1": {"cpu": "6", "ram": "16384", "disk": "20"},
        "testvm2": {"cpu": "6", "ram": "16384", "disk": "20"},
        "testvm3": {"cpu": "6", "ram": "16384", "disk": "20"},
        "testvm4": {"cpu": "6", "ram": "16384", "disk": "20"},
        "testvm5": {"cpu": "6", "ram": "16384", "disk": "20"},
    }
VMS = list(VMS_DATES.keys())
VMS_GROUP = {
    "all": VMS,
    "clients_group": ["testvm2", "testvm3", "testvm4", "testvm5"],
}
USER = "u"
PASSWORD = "1"

TEMPLATE_PATH = "/home/u/git/stress_test/astra_openvpn/ovpn/template"

CLIENTS_TOTAL = 250

if VERSION_OS.startswith("1.7"):
    DEV = "eth0"
elif VERSION_OS.startswith("1.8"):
    DEV = "enp1s0"

# 0 means auto duration based on ramp-up time.
STATS_DURATION_SECONDS = 0
# Extra seconds added to auto duration.
STATS_EXTRA_SECONDS = 180

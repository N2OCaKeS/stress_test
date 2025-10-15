from allta import SystemCommands

# Build Settings

VERSION_OS = SystemCommands.check_output_command("cat /etc/astra_version")
BOX = "1.8.1.o" if VERSION_OS == "1.8" else "1.7.5.o"
KERNEL = SystemCommands.check_output_command("uname -r")

vm_params = {"cpu": "3", "ram": "16384", "disk": "20"},

VMS_DATES = {
    "testvm1": vm_params,
    "testvm2": vm_params,
    "testvm3": vm_params,
    "testvm4": vm_params,
    "testvm5": vm_params,
}

VMS = list(VMS_DATES.keys())
VMS_GROUP = {"all": VMS, "clients_group": ["testvm2", "testvm3", "testvm4", "testvm5"]}
USER = "u"
PASSWORD = "1"

TEMPLATE_PATH = "/home/u/git/stress_test/new_astra_openvpn/ovpn/template"

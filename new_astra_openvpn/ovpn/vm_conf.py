from allta import SystemCommands
VERSION_OS = SystemCommands.check_output_command("cat /etc/astra_version")
KERNEL = SystemCommands.check_output_command("uname -r")

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

TEMPLATE_PATH = "/home/u/git/stress_test/new_astra_openvpn/ovpn/template"

CLIENTS_TOTAL = 400
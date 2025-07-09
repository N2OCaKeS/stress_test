from allta import SystemCommands
import requests

sys_cls = SystemCommands()

jira_url_api = 'http://allta.devos.astralinux.ru/rest/api/get-jira-url'
confluence_url_api = 'http://allta.devos.astralinux.ru/rest/api/get-confluence-url'
response_jira_url = requests.get(jira_url_api)
response_confluence_url = requests.get(confluence_url_api)
JIRA_URL = response_jira_url.text
CONFLUENCE_URL = response_confluence_url.text

# VMS DATES
VMS_DATES = {  # Полный список ВМ
    'testvm1': {'host-port': '22',
                'cpu': '3',
                'ram': '16384'},
    'testvm2': {'host-port': '22',
                'cpu': '3',
                'ram': '16384'},
    'testvm3': {'host-port': '22',
                'cpu': '3',
                'ram': '16384'},
    'testvm4': {'host-port': '22',
                'cpu': '3',
                'ram': '16384'},
    'testvm5': {'host-port': '22',
                'cpu': '3',
                'ram': '16384'}
}

# Test №1
USER = ["u", "askeladd"]
RANGE = 1200
DURATION_RATE = RANGE
VMS_COUNT = 5
VMS = [f"testvm{i}" for i in range(1, VMS_COUNT+1)]
CONNECTIONS_PER_MINUTE = 120 // (VMS_COUNT - 1)

# Common dirs/files
OVPN_PATH = f"/home/{USER[1]}/git/stress_test/astra_openvpn"
VENV_PATH = f"/home/{USER[1]}/python/Python-3.12.1/venv/bin/activate"
REPORT_PATH = f"{OVPN_PATH}/results"
TEMPLATE_PATH = f"{OVPN_PATH}/templates"

COLORS = {
    "GREEN": "\033[32m",
    "RED": "\033[31m",
    "RESET": "\033[0m"
}

VM_INFONAME = 'av.info'
VM_KERNEL = 'kernel.info'
VM_RESULTS_PATH = f"{REPORT_PATH}/vm_results"

BOX_VERSIONS = [["1.7.5.o", "1.7.5.v", "1.7.5.s"], ["1.8.1.o", "1.8.1.v", "1.8.1.s"]]
BOXES = f"{OVPN_PATH}/box-config.json"
DATES = f"/home/{USER[0]}/dates_stand3.conf"



#----------INFO-----------

INFO_FILENAME = f'{REPORT_PATH}/INFO.txt'
RC = sys_cls.check_output_command("cat /etc/astra/build_version | tr -d '[:space:]'")
KERNEL = sys_cls.check_output_command("uname -r | tr -d '[:space:]'")
PACKAGE = (f"astra-openvpn-server_{sys_cls.check_output_command('dpkg -l | grep astra-openvpn-server | awk \'$2 == \"astra-openvpn-server\" {print $3}\'')}, "
           f"openvpn_{sys_cls.check_output_command('dpkg -l | grep openvpn | awk \'$2 == \"openvpn\" {print $3}\'')}, "
           f"iperf_{sys_cls.check_output_command('dpkg -l | grep iperf | awk \'$2 == \"iperf\" {print $3}\'')}")

VERSION_OS = ".".join(RC.split(".")[:2])
MODIFY = ["o", "s", "v"]
BOX = "1.8.1.o" if VERSION_OS == "1.8" else "1.7.5.o"
print(BOX)

# print(SYS_VERSION, SYS_KERNEL, PACKAGE, sep="\n")
print(RC)
print(".".join(RC.split(".")[:2]))

print(VERSION_OS)







from allta import SystemCommands
import requests
sys_com = SystemCommands

jira_url_api = 'http://allta.devos.astralinux.ru/rest/api/get-jira-url'
confluence_url_api = 'http://allta.devos.astralinux.ru/rest/api/get-confluence-url'
response_jira_url = requests.get(jira_url_api)
response_confluence_url = requests.get(confluence_url_api)
JIRA_URL = response_jira_url.text
CONFLUENCE_URL = response_confluence_url.text

# VMS DATES
VMS_DATES = {  # Полный список ВМ
    'testvm1': {'host-port': '22',
                  'cpu': '8',
                  'ram': '32768'},
    'testvm2': {'host-port': '22',
                  'cpu': '8',
                  'ram': '32768'},
    'testvm3': {'host-port': '22',
                  'cpu': '8',
                  'ram': '32768'},
    'testvm4': {'host-port': '22',
              'cpu': '8',
              'ram': '32768'},
}

# Test №1
USER = ["u", "askeladd"]
RANGE = 2100
DURATION_RATE = 1050
VM_COUNT = 4
VMS = [f"testvm{i}" for i in range(1, VM_COUNT+1)]

# Common dirs
OVPN_PATH = f"/home/{USER[0]}/git/stress_test/astra_openvpn"
VENV_PATH = f"/home/{USER[0]}/python/Python-3.12.1/venv/bin/activate"
REPORT_PATH = f"{OVPN_PATH}/results"

INFO_FILENAME = 'ovpn_info.txt'
VM_INFONAME = 'av.info'
VM_KERNEL = 'kernel.info'
VM_RESULTS_PATH = f"{REPORT_PATH}/vm_results"

BOX_VERSIONS = [["1.7.5.o", "1.7.5.v", "1.7.5.s"], ["1.8.1.o", "1.8.1.v", "1.8.1.s"]]
BOXES = f"{OVPN_PATH}/box-config.json"
DATES = f"/home/{USER[0]}/dates_stand3.conf"

SYS_VERSION = sys_com.check_output_command("cat /etc/astra/build_version | tr -d '[:space:]'")
SYS_KERNEL = sys_com.check_output_command("uname -r | tr -d '[:space:]'")
SYS_VERSION_MOD = [SYS_VERSION+".o", SYS_VERSION+".v", SYS_VERSION+".s"]









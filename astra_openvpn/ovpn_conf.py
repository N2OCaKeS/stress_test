from allta import SystemCommands
import requests
sys_com = SystemCommands

jira_url_api = 'http://allta.devos.astralinux.ru/rest/api/get-jira-url'
confluence_url_api = 'http://allta.devos.astralinux.ru/rest/api/get-confluence-url'
response_jira_url = requests.get(jira_url_api)
response_confluence_url = requests.get(confluence_url_api)
JIRA_URL = response_jira_url.text
CONFLUENCE_URL = response_confluence_url.text

USER = ["u", "askeladd"]
RANGE = 1000
DURATION_RATE = 1000

OVPN_PATH = f"/home/{USER[0]}/git/stress_test/astra_openvpn"
VENV_PATH = f"/home/{USER[0]}/python/Python-3.12.1/venv/bin/activate"
INFO_FILENAME = 'ovpn_info.txt'
VM_INFONAME = 'av.info'
VM_KERNEL = 'kernel.info'
REPORT_PATH = f"{OVPN_PATH}/results"
VM_RESULTS_PATH = f"{REPORT_PATH}/vm_results"

BOX_VERSIONS = [["1.7.5.o", "1.7.5.v", "1.7.5.s"], ["1.8.1.o", "1.8.1.v", "1.8.1.s"]]
BOXES = f"{OVPN_PATH}/box-config.json"
DATES = f"/home/{USER[0]}/dates_stand3.conf"

SYS_VERSION = sys_com.check_output_command("cat /etc/astra/build_version | tr -d '[:space:]'")
SYS_KERNEL = sys_com.check_output_command("uname -r | tr -d '[:space:]'")
SYS_VERSION_MOD = [SYS_VERSION+".o", SYS_VERSION+".v", SYS_VERSION+".s"]









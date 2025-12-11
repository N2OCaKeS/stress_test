import requests

jira_url_api = 'http://allta.devos.astralinux.ru/rest/api/get-jira-url'
confluence_url_api = 'http://allta.devos.astralinux.ru/rest/api/get-confluence-url'
response_jira_url = requests.get(jira_url_api)
response_confluence_url = requests.get(confluence_url_api)
JIRA_URL = response_jira_url.text
CONFLUENCE_URL = response_confluence_url.text

GRAPH_DESCRIPTIONS = {'cgraph.png': '<p style="font-family: Century Gothic, sans-serif; font-size: 14px;">График количества активаций клиентских туннелей на минуту теста.<ul><li><b>OX</b>: Время выполнения теста(минуты);</li><li><b>OY</b>: Количество успешно подключенных туннелей к серверу;</li></ul></p>'}
  
# Common dirs/files
OVPN_PATH = "/home/u/git/stress_test/astra_openvpn"
VENV_PATH = "/home/u/python/Python-3.12.1/venv/bin/python3.12"
REPORT_PATH = f"{OVPN_PATH}/results"
TEMPLATE_PATH = f"{OVPN_PATH}/templates"

COLORS = {
    "GREEN": "\033[32m",
    "RED": "\033[31m",
    "RESET": "\033[0m"
}

VM_RESULTS_PATH = f"{REPORT_PATH}/vm_results"

MODIFY = ["o", "s"]
#----------INFO-----------

# INFO_FILENAME = f'{REPORT_PATH}/INFO.txt'
# RC = sys_cls.check_output_command("cat /etc/astra/build_version | tr -d '[:space:]'")
# KERNEL = sys_cls.check_output_command("uname -r | tr -d '[:space:]'")
# ASTRA_PKG = sys_cls.check_output_command(
#     "dpkg -l | grep astra-openvpn-server | awk '$2 == \"astra-openvpn-server\" {print $3}'"
# )
# OPENVPN_PKG = sys_cls.check_output_command(
#     "dpkg -l | grep openvpn | awk '$2 == \"openvpn\" {print $3}'"
# )
# IPERF_PKG = sys_cls.check_output_command(
#     "dpkg -l | grep iperf | awk '$2 == \"iperf\" {print $3}'"
# )

# PACKAGE = (
#     f"astra-openvpn-server_{ASTRA_PKG}, "
#     f"openvpn_{OPENVPN_PKG}, "
#     f"iperf_{IPERF_PKG}"
# )



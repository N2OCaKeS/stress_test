import os
import requests
from pathlib import Path

jira_url_api = 'http://allta.devos.astralinux.ru/rest/api/get-jira-url'
confluence_url_api = 'http://allta.devos.astralinux.ru/rest/api/get-confluence-url'
response_jira_url = requests.get(jira_url_api)
response_confluence_url = requests.get(confluence_url_api)
JIRA_URL = response_jira_url.text
CONFLUENCE_URL = response_confluence_url.text
VENV_PATH = '/home/u/python/Python-3.12.1/venv/bin/python3.12'

BASE_PATH = "/home/u/git/stress_test/network"

# Confluence
REPORT_PATH = f'{os.getcwd()}/test_results'

#VM Settings
USERNAME = "u"
PASSWORD = "1"

# Base params
# Create dir if not created
VM_OS_INFO_PATH = f"{BASE_PATH}/vm_info"
Path(VM_OS_INFO_PATH).mkdir(mode=0o777, exist_ok=True)
VM_INFONAME = f'{VM_OS_INFO_PATH}/av.txt'
VM_KERNEL = f'{VM_OS_INFO_PATH}/kernel.txt'

# Test Params

# Kernel Network
KERNEL_NET_VM_COUNT = 2
KERNEL_NET_VCPU = 8
KERNEL_NET_RAM = 16384

# Load params
IOF_RESULTS = 'iof_results.json'
IOF_OFF_NAME = 'results_iof_off.txt'
IOF_ON_NAME = 'results_iof_on.txt'
IOF_OFF_PATH = f'/home/u/{IOF_OFF_NAME}'
IOF_ON_PATH = f'/home/u/{IOF_ON_NAME}'
ITERATIONS = 10

# DHCP (kea-dhcp4-server), BT-T16055 / BT-T16062
# 1 сервер kea + 4 клиента perfdhcp, та же сеть test, что создаёт allta
DHCP_VM_COUNT = 5
DHCP_VCPU = 4
DHCP_RAM = 8192

DHCP_SERVER_VM = "testvm1"
# та же подсеть, что у сети test в allta (_virt_install.py: 192.168.100.1/24) -
# берём адреса вне .1 (мост) и вне старого DHCP-пула гипервизора .128-.254
DHCP_SUBNET = "192.168.100.0/24"
DHCP_NETMASK = "255.255.255.0"
DHCP_SERVER_IP = "192.168.100.10"
# testvm2..testvm5 -> заранее зарезервированные в kea по MAC адреса
DHCP_CLIENT_IPS = {
    "testvm2": "192.168.100.11",
    "testvm3": "192.168.100.12",
    "testvm4": "192.168.100.13",
    "testvm5": "192.168.100.14",
}

KEA_SERVER_PACKAGES = ["kea-dhcp4-server"]
KEA_CLIENT_PACKAGES = ["kea-common", "kea-admin"]  # kea-admin тянет perfdhcp

DHCP_CONF_DIR = f"{BASE_PATH}/dhcp_conf"
Path(DHCP_CONF_DIR).mkdir(mode=0o777, exist_ok=True)
DHCP_CONF_LOCAL_PATH = f"{DHCP_CONF_DIR}/kea-dhcp4.conf"
DHCP_CONF_REMOTE_PATH = "/etc/kea/kea-dhcp4.conf"

# Время ожидания загрузки ВМ после сетевого cutover-а
DHCP_SERVER_BOOT_SLEEP = 90
DHCP_CLIENT_BOOT_SLEEP = 60
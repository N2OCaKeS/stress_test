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
Path(REPORT_PATH).mkdir(mode=0o777, exist_ok=True)

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

# DHCP (kea-dhcp4-server)

DHCP_VM_COUNT = 5
DHCP_VCPU = 4
DHCP_RAM = 8192
DHCP_ITERATIONS = 10

DHCP_SERVER_VM = "testvm1"

DHCP_SUBNET = "192.168.0.0/15"
DHCP_NETMASK = "255.254.0.0"
DHCP_SERVER_IP = "192.168.100.10"
# testvm2..testvm5 -> заранее зарезервированные в kea по MAC адреса
DHCP_CLIENT_IPS = {
    "testvm2": "192.168.100.11",
    "testvm3": "192.168.100.12",
    "testvm4": "192.168.100.13",
    "testvm5": "192.168.100.14",
}

KEA_SERVER_PACKAGES = ["kea-dhcp4-server"]
KEA_CLIENT_PACKAGES = ["kea-common", "kea-admin"]  # kea-admin для perfdhcp

DHCP_CONF_DIR = f"{BASE_PATH}/dhcp_conf"
Path(DHCP_CONF_DIR).mkdir(mode=0o777, exist_ok=True)
DHCP_CONF_LOCAL_PATH = f"{DHCP_CONF_DIR}/kea-dhcp4.conf"
DHCP_CONF_REMOTE_PATH = "/etc/kea/kea-dhcp4.conf"

DHCP_POOL_START = "192.168.101.0"
DHCP_POOL_END = "192.169.255.254"

DHCP_LOAD_CLIENT_VM = "testvm2"

DHCP_PERFDHCP_RATE = 2000
DHCP_PERFDHCP_CLIENT_STEPS = [5000, 10000, 20000, 40000, 80000]

DHCP_PERFDHCP_REMOTE_PATH = "/home/u/perfdhcp_result.txt"
DHCP_PERFDHCP_LOCAL_NAME = "perfdhcp_result.txt"

DHCP_PERFDHCP_STEP_MARKER_PREFIX = "=== perfdhcp step N="

DHCP_KEA_STATS_BEFORE_REMOTE = "/home/u/kea_stats_before.json"
DHCP_KEA_STATS_AFTER_REMOTE = "/home/u/kea_stats_after.json"

DHCP_KEA_PROC_STATS_REMOTE = "/home/u/kea_proc_stats.txt"

DHCP_RESULTS = f"{REPORT_PATH}/dhcp_results.json"
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
if Path(VM_OS_INFO_PATH).is_dir:
    pass
else:
    os.mkdir(VM_OS_INFO_PATH, mode=777)
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
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

BASE_PATH = "/home/u/git/stress_test/kernel"

# Confluence
REPORT_PATH = f'{os.getcwd()}/test_results'

#VM Settings
USERNAME = "u"
PASSWORD = "1"

# Base params
# Create dir if not created
VM_OS_INFO_PATH = f"{BASE_PATH}/vm_info"
Path(VM_OS_INFO_PATH).mkdir(mode=0o777, parents=True, exist_ok=True)
VM_INFONAME = f'{VM_OS_INFO_PATH}/av.txt'
VM_KERNEL = f'{VM_OS_INFO_PATH}/kernel.txt'

VM_TEST1_OUTPUT = f'{BASE_PATH}/test1_output.txt'
VM_TEST2_OUTPUT = f'{BASE_PATH}/test2_output.txt'
RESULTS_FILE = 'results.json'

# Test Params

# Kernel Network
SEGMENTATION_FAULT_VM_COUNT = 1
SEGMENTATION_FAULT_VCPU = 4
SEGMENTATION_FAULT_RAM = 4096

XFS_MEMORY_LEAK_VM_COUNT = 1
XFS_MEMORY_LEAK_VCPU = 4
XFS_MEMORY_LEAK_RAM = 8192
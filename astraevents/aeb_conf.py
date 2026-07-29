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

BASE_PATH = "/home/u/git/stress_test/astraevents"

# Confluence
REPORT_PATH = f'{os.getcwd()}/test_results'

#VM Settings
USERNAME = "u"
PASSWORD = "1"

# Base params
# Create dir if not created
VM_OS_INFO_PATH = f"{BASE_PATH}/vm_info"
Path(VM_OS_INFO_PATH).mkdir(mode=0o777, parents=True, exist_ok=True)


PRIMARY_VM = "testvm1"
VM_INFONAME = f'{VM_OS_INFO_PATH}/{PRIMARY_VM}/av.txt'
VM_KERNEL = f'{VM_OS_INFO_PATH}/{PRIMARY_VM}/kernel.txt'


VM_RESULTS_PATH = f"{BASE_PATH}/results"

VM_COUNT = 2
VCPU_MIN = 2
RAM_MIN = 4096

VCPU_MAX = 16
RAM_MAX = 16384
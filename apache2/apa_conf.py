import requests
from pathlib import Path

jira_url_api = 'http://allta.devos.astralinux.ru/rest/api/get-jira-url'
confluence_url_api = 'http://allta.devos.astralinux.ru/rest/api/get-confluence-url'
response_jira_url = requests.get(jira_url_api)
response_confluence_url = requests.get(confluence_url_api)
JIRA_URL = response_jira_url.text
CONFLUENCE_URL = response_confluence_url.text

SCRIPT_DIR = '/home/u/git/stress_test/apache2'
VM_PATH = '/home/u'
REPORT_PATH = f'{VM_PATH}/report'
TEMPLATE_PATH = f'{SCRIPT_DIR}/templates'
CSV_RESULTS_FILE = f'{REPORT_PATH}/percentages.csv'
PLOT_FILE = f'{REPORT_PATH}/values.tsv'
AB_OUTPUT_FILE_PAM = f'{REPORT_PATH}/summary_pam.txt'
AB_OUTPUT_FILE_NOPAM = f'{REPORT_PATH}/summary_no-pam.txt'
AB_OUTPUT_FILE_BALANCE = f'{REPORT_PATH}/summary_balance.txt'
# Compatibility names for older intermediate balance code. The VIP test uses
# only AB_OUTPUT_FILE_BALANCE/BALANCE_RESULTS as the single benchmark result.
AB_OUTPUT_FILE_BALANCE_LB1 = f'{REPORT_PATH}/summary_balance_testvm1.txt'
AB_OUTPUT_FILE_BALANCE_LB2 = f'{REPORT_PATH}/summary_balance_testvm2.txt'
NOPAM_RESULTS = f'{SCRIPT_DIR}/summary_no-pam.txt'
PAM_RESULTS = f'{SCRIPT_DIR}/summary_pam.txt'
BALANCE_RESULTS = f'{SCRIPT_DIR}/summary_balance.txt'
BALANCE_RESULTS_LB1 = f'{SCRIPT_DIR}/summary_balance_testvm1.txt'
BALANCE_RESULTS_LB2 = f'{SCRIPT_DIR}/summary_balance_testvm2.txt'
REPORT_FILENAME = 'report.txt'
INFO_FILENAME = 'ap_info.txt'
VENV_PATH = '/home/u/python/Python-3.12.1/venv/bin/python3.12'


#VM Settings
USERNAME = "u"
PASSWORD = "1"
ABP_VM_COUNT = 2
ABP_VCPU = 16
ABP_RAM = 32768

#Количество запусков бенчмарка
REPETITIONS_COUNTER = 30
#Запросы
REQUESTS = 200000
#Потоки
CONCURRENCY = 200
#Лимит группы по количеству элементов, принимаемой к расчетам, в %
VALID_VALUES_PERCENT = 50
#Лимит отклонения, в %
PERCENT_LIMIT = 10


# ip address of tested server with apache2
TESTED_SERVER_IP = "10.0.20.23"
CLIENT_IP = "10.0.20.20"


# ssh ports  
TESTED_SERVER_SSH_PORT = 22
CLIENT_SSH_PORT = 22

# admin login creds for tested server
TESTED_SERVER_ADMIN_USER = "u"
TESTED_SERVER_ADMIN_PASS = "1"

CLIENT_ADMIN_USER = "u"
CLIENT_ADMIN_PASS = "1"

TESTED_QA_USER = "qa0"
TESTED_QA_USER_MAC = "qa1"
TESTED_QA_USER_MAC_CAT = "qa2"
TESTED_SERVER_QA_PASS = "1"

# tested server creds for connection instance
TESTED_SERVER_ADMIN_CREDS = {"password": TESTED_SERVER_ADMIN_PASS}
TESTED_SERVER_QA_CREDS = {"password": TESTED_SERVER_ADMIN_PASS}

CLIENT_ADMIN_CREDS = {"password": CLIENT_ADMIN_PASS}


# settings for apache benchmark 
MAX_CONCURRENCY = 201
CONCURRENCY_STEP = 50

MAX_REQUESTS = 2500

# APACHE BALANCE
A_BALANCE_VM_COUNT = 5
A_BALANCE_VCPU = 16
A_BALANCE_RAM = 32768
A_BALANCE_LB_COUNT = 2
A_BALANCE_BACKEND_COUNT = 2
A_BALANCE_CLIENT_COUNT = 1
A_BALANCE_VIP = "10.0.20.250"
A_BALANCE_KEEPALIVED_VRID = 51
A_BALANCE_KEEPALIVED_AUTH_PASS = "apachebalance"
A_BALANCE_KEEPALIVED_LB1_PRIORITY = 150
A_BALANCE_KEEPALIVED_LB2_PRIORITY = 100
A_BALANCE_KEEPALIVED_CHECK_INTERVAL = 2


# Base params
# Create dir if not created
VM_OS_INFO_PATH = f"{SCRIPT_DIR}/vm_info"
Path(VM_OS_INFO_PATH).mkdir(mode=0o777, parents=True, exist_ok=True)
VM_INFONAME = f'{VM_OS_INFO_PATH}/av.txt'
VM_KERNEL = f'{VM_OS_INFO_PATH}/kernel.txt'

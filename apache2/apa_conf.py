import requests

jira_url_api = 'http://bendiks.devos.astralinux.ru/rest/api/get-jira-url'
confluence_url_api = 'http://bendiks.devos.astralinux.ru/rest/api/get-confluence-url'
response_jira_url = requests.get(jira_url_api)
response_confluence_url = requests.get(confluence_url_api)
JIRA_URL = response_jira_url.text
CONFLUENCE_URL = response_confluence_url.text

SCRIPT_DIR = '/home/u/git/stress_test/apache2'
REPORT_PATH = f'{SCRIPT_DIR}/report'
TEMPLATE_PATH = f'{SCRIPT_DIR}/templates'
REPORT_FILENAME = 'report.txt'
INFO_FILENAME = 'ap_info.txt'
VENV_PATH = '/home/u/python/Python-3.12.1/venv/bin/python3.12'


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
MAX_CONCURRENCY = 200
CONCURRENCY_STEP = 25

MAX_REQUESTS = 2500





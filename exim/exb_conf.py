import requests

jira_url_api = 'http://allta.devos.astralinux.ru/rest/api/get-jira-url'
confluence_url_api = 'http://allta.devos.astralinux.ru/rest/api/get-confluence-url'
response_jira_url = requests.get(jira_url_api)
response_confluence_url = requests.get(confluence_url_api)
JIRA_URL = response_jira_url.text
CONFLUENCE_URL = response_confluence_url.text


MAIL_START = 200
MAIL_STEP = 200
MAIL_MAX = 1000

MAX_WORKERS = 10

REPORT_PATH = ""


SCRIPT_DIR = '/home/u/git/stress_test/exim'
LOG_FILENAME = 'exb_log'
LOG_PATH = '{}/{}'.format(SCRIPT_DIR, LOG_FILENAME)
REPORT_PATH = '{}/report'.format(SCRIPT_DIR)
REPORT_FILENAME = f'{REPORT_PATH}/exb_report.txt'
TEMPLATE_PATH = '{}/templates'.format(SCRIPT_DIR)
INFO_FILENAME = 'exb_info.txt'
VENV_PATH = '/home/u/python/Python-3.12.1/venv/bin/python3.12'
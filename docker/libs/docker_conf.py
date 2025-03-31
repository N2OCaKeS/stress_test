import os
import requests


jira_url_api = 'http://allta.devos.astralinux.ru/rest/api/get-jira-url'
confluence_url_api = 'http://allta.devos.astralinux.ru/rest/api/get-confluence-url'
response_jira_url = requests.get(jira_url_api)
response_confluence_url = requests.get(confluence_url_api)
JIRA_URL = response_jira_url.text
CONFLUENCE_URL = response_confluence_url.text
REPORT_PATH = f'{os.getcwd()}/results/'
# TEMPLATE_PATH = f'{os.getcwd()}/templates'
VENV_PATH = '/home/u/python/Python-3.12.1/venv/bin/python3.12'
UB_PATH = f'{os.getcwd()}/byte-unixbench-master'
UB_ARHIVE = f'{UB_PATH}/unixbench.zip'
UB_RESULTS = f'{UB_PATH}/UnixBench/results'

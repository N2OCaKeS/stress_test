import os
import requests

jira_url_api = "http://allta.devos.astralinux.ru/rest/api/get-jira-url"
confluence_url_api = "http://allta.devos.astralinux.ru/rest/api/get-confluence-url"
response_jira_url = requests.get(jira_url_api)
response_confluence_url = requests.get(confluence_url_api)
JIRA_URL = response_jira_url.text
CONFLUENCE_URL = response_confluence_url.text

SCRIPT_DIR = "/home/u/git/stress_test/docker"
REPORT_PATH = f"{SCRIPT_DIR}/site/results"
VENV_PATH = "/home/u/python/Python-3.12.1/venv/bin"
TEMPLATE_PATH = f"{SCRIPT_DIR}/templates"
INFO_FILENAME = f"{REPORT_PATH}/INFO.txt"
DESCRIPTION = ""
WORKER = 5

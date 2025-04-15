import requests

jira_url_api = "http://allta.devos.astralinux.ru/rest/api/get-jira-url"
confluence_url_api = "http://allta.devos.astralinux.ru/rest/api/get-confluence-url"
response_jira_url = requests.get(jira_url_api)
response_confluence_url = requests.get(confluence_url_api)
JIRA_URL = response_jira_url.text
CONFLUENCE_URL = response_confluence_url.text

SCRIPT_DIR = "/home/u/git/stress_test/docker"
REPORT_PATH = f"{SCRIPT_DIR}/site/results"
REPORT_VARIABLES = ["nginx_docker", "nginx_server", "locust_proc"]
VENV_PATH = "/home/u/python/Python-3.12.1/venv/bin"
TEMPLATE_PATH = f"{SCRIPT_DIR}/templates"
INFO_FILENAME = f"{REPORT_PATH}/INFO.txt"
RT_FACTOR = 5000
USERS_PER_SEC, TIMESTEP = 100, 10
NORMALIZED_CONSTANTS = {"Avg Requests/s": [0, 6000],
                        "Avg Failures/s": [0, 5000],
                        "Avg Response Time": [0, 100]}
DESCRIPTION = ""



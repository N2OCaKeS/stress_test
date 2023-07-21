import requests


def response():
    jira = requests.get('https://jira.astralinux.ru').status_code
    life = requests.get('https://life.astralinux.ru').status_code
    return jira, life


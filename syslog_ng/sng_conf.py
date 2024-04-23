import requests

jira_url_api = 'http://bendiks.devos.astralinux.ru/rest/api/get-jira-url'
confluence_url_api = 'http://bendiks.devos.astralinux.ru/rest/api/get-confluence-url'
response_jira_url = requests.get(jira_url_api)
response_confluence_url = requests.get(confluence_url_api)
JIRA_URL = response_jira_url.text
CONFLUENCE_URL = response_confluence_url.text

SERVICE_COUNT = 4800 # 4800
TIME_EXEC = 90 # 1440
TIME_EXEC_ST3_ST4 = 180
SCRIPT_DIR = "/home/u/git/stress_test/syslog_ng"
REPORT_PATH = "{}/report".format(SCRIPT_DIR)
REPORT_FILENAME = 'main_report.html'
TEMPLATE_PATH = "{}/templates".format(SCRIPT_DIR)
IMAGE_WIDTH = 23 # 23
IMAGE_HEIGHT = 16 # 16
VENV_PATH = '/home/u/python/Python-3.12.1/venv/bin/python3.12'

INFO_FILENAME = '{}/sng_info.txt'.format(REPORT_PATH)

'''
    Описание для графиков отчета
'''
GRAPH_DESCRIPTIONS = {
    'sng_cpu.png': '<p style="font-family: Century Gothic, sans-serif; font-size: 14px;">График зависимости загрузки ЦПУ от количества прошедших секунд.<ul><li><b>OX</b>: Количество секунд после запуска тестового сценария;</li><li><b>OY</b>: Загрузка ЦПУ;</li><li><b>Функция</b>: Аппроксимирующая функция точек;</li></ul></p>',
    'sng_memory.png': '<p style="font-family: Century Gothic, sans-serif; font-size: 14px;">График зависимости загрузки оперативной памяти от количества прошедших секунд.<ul><li><b>OX</b>: Количество секунд после запуска тестового сценария;</li><li><b>OY</b>: Загрузка оперативной памяти;</li><li><b>Функция</b>: Аппроксимирующая функция точек</li></ul></p>',
    'sng_syslog_memory.png': '<p style="font-family: Century Gothic, sans-serif; font-size: 14px;">График зависимости загрузки оперативной памяти сервисом syslog-ng от количества прошедших секунд.<ul><li><b>OX</b>: Количество секунд после запуска тестового сценария;</li><li><b>OY</b>: Загрузка оперативной памяти сервисом syslog-ng;</li><li><b>Функция</b>: Аппроксимирующая функция точек</li></ul></p>',
    'sng_disk.png': '<p style="font-family: Century Gothic, sans-serif; font-size: 14px;">График зависимости загрузки диска (дисковое заполнение) от количества прошедших секунд.<ul><li><b>OX</b>: Количество секунд после запуска тестового сценария;</li><li><b>OY</b>: Загрузка диска (дисковое наполнение)</li><li><b>Функция</b>: Аппроксимирующая функция точек</li></ul></p>',
}


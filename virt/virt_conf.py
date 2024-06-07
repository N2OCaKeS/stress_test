import os
import requests

jira_url_api = 'http://allta.devos.astralinux.ru/rest/api/get-jira-url'
confluence_url_api = 'http://allta.devos.astralinux.ru/rest/api/get-confluence-url'
response_jira_url = requests.get(jira_url_api)
response_confluence_url = requests.get(confluence_url_api)
JIRA_URL = response_jira_url.text
CONFLUENCE_URL = response_confluence_url.text
REPORT_PATH = f'{os.getcwd()}/test_results'
TEMPLATE_PATH = f'{os.getcwd()}/templates'
FIO_PATH = f'{os.getcwd()}/fio'
INFO_FILENAME = 'virt_info.txt'
VM_INFONAME = 'av.info'
VM_KERNEL = 'kernel.info'
VENV_PATH = '/home/u/python/Python-3.12.1/venv/bin/python3.12'
UB_PATH = f'{os.getcwd()}/byte-unixbench-master'
UB_ARHIVE = f'{UB_PATH}/unixbench.zip'
UB_RESULTS = f'{UB_PATH}/UnixBench/results'


#StealTime
LOW = 1         #TEST_MASHINES --- # count fot middleserver
HIGH = 70       #TEST_MASHINES --- # count fot middleserver
ST_vCPU = 2     #Steal Time vCPU
ST_RAM = 2048   #Steal Time RAM


#Flexible I/O tester
FIOVERS_17x = 'fio_3.12-2_amd64.deb'
FIOVERS_18x = 'fio_3.33-3_amd64.deb'
FIO_vCPU = 16
FIO_RAM = 32768
BLOCK_SIZE = '4k'
FILE_SIZE = '10G'
IO_DEPTH_1 = 1
IO_DEPTH_128 = 128


#UnixBench
STEP = 4
LOW_COPIES = 4
HIGH_COPIES = 16
UB_vCPU = 8
UB_RAM = 16384
UB_RESULT_HTML = 'results.html'


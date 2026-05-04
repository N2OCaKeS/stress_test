import requests

jira_url_api = 'http://allta.devos.astralinux.ru/rest/api/get-jira-url'
confluence_url_api = 'http://allta.devos.astralinux.ru/rest/api/get-confluence-url'
response_jira_url = requests.get(jira_url_api)
response_confluence_url = requests.get(confluence_url_api)
JIRA_URL = response_jira_url.text
CONFLUENCE_URL = response_confluence_url.text

SCRIPT_DIR = '/home/u/git/stress_test/parsec'
REPORT_PATH = f'{SCRIPT_DIR}/report'
TEMPLATE_PATH = f'{SCRIPT_DIR}/templates'

FLAMEGRAPH_NAME = 'spinlock_flamegraph.svg'
REPORT_FILENAME = 'spinlock_results.json'
TIMEDF_NAME = f'{TEMPLATE_PATH}/sl_timedf.html'
TOTALDF_NAME = f'{TEMPLATE_PATH}/sl_totaldf.html'
DETAILDF_NAME = f'{TEMPLATE_PATH}/sl_detaildf.html'

INFO_FILENAME = 'sl_info.txt'

UB_PATH = '/home/u/git/stress_test/parsec/byte-unixbench-master/UnixBench'
UB_CONCURRENCY = 4

PERF_FREQ = 99

SPINLOCK_PATTERN = r'_raw_spin_lock|queued_spin_lock|_raw_spin_trylock'

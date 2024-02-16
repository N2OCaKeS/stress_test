SCRIPT_DIR = '/home/u/git/stress_test/parsec'
REPORT_FILENAME = 'test_results.json'
FLAMEGRAPH_NAME = 'result_flamegraph.svg'
REPORT_PATH = f'{SCRIPT_DIR}/report'
TEMPLATE_PATH = f'{SCRIPT_DIR}/templates'
TIMEDF_NAME = f'{TEMPLATE_PATH}/timedf.html'
TOTALDF_NAME = f'{TEMPLATE_PATH}/totaldf.html'
DETAILDF_NAME = f'{TEMPLATE_PATH}/detaildf.html'
INFO_FILENAME = 'ps_info.txt'
VENV_PATH = '/home/u/python/Python-3.12.1/venv/bin/python3.12'

#количество создаваемых потоков 
CONC = 100
#количество циклов для каждого потока
COUNTER = 100000

FILE_SYSTEM = 'tmpfs'


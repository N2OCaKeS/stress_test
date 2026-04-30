from os import path


VENV_PATH = '/home/u/python/Python-3.12.1/venv/bin/python3.12'
MAIN_DIR = path.normpath(path.join(path.dirname(path.abspath(__file__)), '..'))


#################################################################################
# UNIXBENCH                                                                     #
#################################################################################
LOW_CONC = 4
HIGH_CONC = 25
STEP = 8

TEST_NAMES = (
    'dhry2reg',
    'whetstone-double',
    'execl',
    'fstime',
    'fsbuffer',
    'fsdisk',
    'pipe',
    'context1',
    'spawn',
    'shell1',
    'shell8',
    'syscall'
)

# Регулярные выражения для парсинга результатов каждого теста
REGEXP_PARSERS = {
    'dhry2reg': r'Dhrystone\s2\susing\sregister\svariables\s*([\d.]+)\slps\s*\(([\d.]+)\ss,\s(\d+)\ssamples',
    'whetstone-double': r'Double-Precision\sWhetstone\s*([\d.]+)\sMWIPS\s*\(([\d.]+)\ss,\s(\d+)\ssamples',
    'execl': r'Execl\sThroughput\s*([\d.]+)\slps\s*\(([\d.]+)\ss,\s(\d+)\ssamples',
    'fstime': r'File\sCopy\s1024\sbufsize\s2000\smaxblocks\s*([\d.]+)\sKBps\s*\(([\d.]+)\ss,\s(\d+)\ssamples',
    'fsbuffer': r'File\sCopy\s256\sbufsize\s500\smaxblocks\s*([\d.]+)\sKBps\s*\(([\d.]+)\ss,\s(\d+)\ssamples',
    'fsdisk': r'File\sCopy\s4096\sbufsize\s8000\smaxblocks\s*([\d.]+)\sKBps\s*\(([\d.]+)\ss,\s(\d+)\ssamples',
    'pipe': r'Pipe\sThroughput\s*([\d.]+)\slps\s*\(([\d.]+)\ss,\s(\d+)\ssamples',
    'context1': r'Pipe-based\sContext\sSwitching\s*([\d.]+)\slps\s*\(([\d.]+)\ss,\s(\d+)\ssamples',
    'spawn': r'Process\sCreation\s*([\d.]+)\slps\s*\(([\d.]+)\ss,\s(\d+)\ssamples',
    'shell1': r'Shell\sScripts\s\(1\sconcurrent\)\s*([\d.]+)\slpm\s*\(([\d.]+)\ss,\s(\d+)\ssamples',
    'shell8': r'Shell\sScripts\s\(8\sconcurrent\)\s*([\d.]+)\slpm\s*\(([\d.]+)\ss,\s(\d+)\ssamples',
    'syscall': r'System\sCall\sOverhead\s*([\d.]+)\slps\s*\(([\d.]+)\ss,\s(\d+)\ssamples',
}

# Регулярное выражение для парсинга общего скора
REGEXP_OVERALL_SCORE = r'System\sBenchmarks\sIndex\sScore\s*([\d.]+)'

# Регулярное выражение для парсинга информации о CPU
REGEXP_CPU_INFO = r'CPU\s\d+:\s([^\(]+)\s\(([\d.]+)\sMHz\)'

# Регулярное выражение для парсинга количества запущенных копий тестов
REGEXP_PARALLEL_COPIES = r'running\s(\d+)\sparallel\scopies'

# Единицы измерения значений каждого теста
TEST_MEASURE = {
    'dhry2reg': 'lps',
    'whetstone-double': 'MWIPS',
    'execl': 'lps',
    'fstime': 'KBps',
    'fsbuffer': 'KBps',
    'fsdisk': 'KBps',
    'pipe': 'lps',
    'context1': 'lps',
    'spawn': 'lps',
    'shell1': 'lpm',
    'shell8': 'lpm',
    'syscall': 'lps',
}


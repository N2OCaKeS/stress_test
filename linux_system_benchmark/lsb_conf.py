
STAND1_LOWER_LIMIT = 2
STAND1_UPPER_LIMIT = 14
STAND1_STEP = 2

STAND2_LOWER_LIMIT = 2
STAND2_UPPER_LIMIT = 14
STAND2_STEP = 2

STAND3_LOWER_LIMIT = 2
STAND3_UPPER_LIMIT = 14
STAND3_STEP = 2

STAND4_LOWER_LIMIT = 2
STAND4_UPPER_LIMIT = 14
STAND4_STEP = 2

SCRIPT_DIR = '/home/u/git/stress_test/linux_system_benchmark'
REPORT_DIR = '{}/report'.format(SCRIPT_DIR)
LOG_DIR = '{}/log'.format(SCRIPT_DIR)

REPORT_FILENAME = 'lsb_report.txt'
INFO_FILENAME = 'lsb_info.txt'

REGEXP_PARSERS = {
    'dhry2reg': r'Dhrystone\s2\susing\sregister\svariables\s*(\d*.\d)\slps\s*\((\d*.\d)\ss,\s(\d*)\ssamples',
    'whetstone-double': r'Double-Precision\sWhetstone\s*(\d*.\d)\sMWIPS\s*\((\d*.\d)\ss,\s(\d*)\ssamples',
    'execl': r'Execl\sThroughput\s*(\d*.\d)\slps\s*\((\d*.\d)\ss,\s(\d*)\ssamples',
    'fstime': r'File\sCopy\s1024\sbufsize\s2000\smaxblocks\s*(\d*.\d)\sKBps\s*\((\d*.\d)\ss,\s(\d*)\ssamples',
    'fsbuffer': r'File\sCopy\s256\sbufsize\s500\smaxblocks\s*(\d*.\d)\sKBps\s*\((\d*.\d)\ss,\s(\d*)\ssamples',
    'fsdisk': r'File\sCopy\s4096\sbufsize\s8000\smaxblocks\s*(\d*.\d)\sKBps\s*\((\d*.\d)\ss,\s(\d*)\ssamples',
    'pipe': r'Pipe\sThroughput\s*(\d*.\d)\slps\s*\((\d*.\d)\ss,\s(\d*)\ssamples',
    'context1': r'Pipe-based\sContext\sSwitching\s*(\d*.\d)\slps\s*\((\d*.\d)\ss,\s(\d*)\ssamples',
    'spawn': r'Process\sCreation\s*(\d*.\d)\slps\s*\((\d*.\d)\ss,\s(\d*)\ssamples',
    'shell1': r'Shell\sScripts\s\(1\sconcurrent\)\s*(\d*.\d)\slpm\s*\((\d*.\d)\ss,\s(\d*)\ssamples',
    'shell8': r'Shell\sScripts\s\(8\sconcurrent\)\s*(\d*.\d)\slpm\s*\((\d*.\d)\ss,\s(\d*)\ssamples',
    'syscall': r'System\sCall\sOverhead\s*(\d*.\d)\slps\s*\((\d*.\d)\ss,\s(\d*)\ssamples',
}

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

TEST_NAMES = ('dhry2reg',
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
              'syscall')
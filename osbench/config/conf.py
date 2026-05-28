from os import path


VENV_PATH = "/home/u/python/Python-3.12.1/venv/bin/python3.12"
MAIN_DIR = path.normpath(path.join(path.dirname(path.abspath(__file__)), '..'))
RESULTS_MAIN_DIR = f"{MAIN_DIR}/results"
RESULTS_STATUS = f"{MAIN_DIR}/logs/test_status.log"
SUBSYSTEM_RESULTS = f"{RESULTS_MAIN_DIR}/subsystem_results.json"


#################################################################################
# CONCURRENCY                                                                    #
#################################################################################
# Потоки - [4, 8, 16]
LOW_CONC = 4
HIGH_CONC = 17
STEP = 8
CONCURRENCY = [LOW_CONC] + list(range(STEP, HIGH_CONC, STEP))
CONCURRENCY_LEN = len(CONCURRENCY)



#################################################################################
# UNIXBENCH                                                                     #
#################################################################################
RESULT_UB_NAME = "unixbench_results.json"

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



#################################################################################
# FS_MARK                                                                       #
#################################################################################
RESULT_FSMARK_NAME = "fs_mark_results.json"
FILES = 10000
FILES_STEP = 10000
FILES_LIMIT = 100001

FILE_SIZE = 1024

FS = 'xfs'
STORAGE_MOUNT_DIR = '/mnt'
INODE_COUNT = '-N 1100000'



#################################################################################
# LMBench                                                                       #
#################################################################################
RESULT_LMBENCH_NAME = "lmbench_results.json"
ITERATIONS_COUNT = CONCURRENCY_LEN



#################################################################################
# PerfBench                                                                     #
#################################################################################
RESULT_PERF_BENCH_NAME = "perf_bench_results.json"



#################################################################################
# Index Criterions                                                              #
#################################################################################
KERNEL_CRITERIONS = {
    # ========== СИСТЕМНЫЕ ВЫЗОВЫ (30%) ==========
    'syscall': {                              # базовые системные вызовы
        'weight': 0.12, 
        'negative': False,
        'bounds': (0.0, 74000000)
        },              
    
    'lat_syscall null': {                     # нулевой syscall - чистая задержка
        'weight': 0.07, 
        'negative': True,
        'bounds': (0.0, 1500)
        },     
    'lat_syscall read': {                     # чтение - частая операция
        'weight': 0.06, 
        'negative': True,
        'bounds': (0.0, 1500)
        },     
    'lat_syscall write': {                    # запись - частая операция
        'weight': 0.05, 
        'negative': True,
        'bounds': (0.0, 1500)
        },    
    
    # ========== ПЛАНИРОВЩИК (25%) ==========
    'sched pipe': {                           # pipe через планировщик
        'weight': 0.09, 
        'negative': True,
        'bounds': (0.0, 3000)
        },           
    'sched messaging': {                      # IPC через планировщик
        'weight': 0.09, 
        'negative': True,
        'bounds': (0.0, 3000)
        },      
    'lat_ctx -s 0 2 4 8 16 24 32 64 128': {   # переключение контекста
        'weight': 0.07, 
        'negative': True,
        'bounds': (0.0, 30000)
        },  
    
    # ========== СИНХРОНИЗАЦИЯ (20%) ==========
    'futex hash': {                           # хэш-таблица с futex
        'weight': 0.08, 
        'negative': False,
        'bounds': (0.0, 50000000)
        },           
    'futex wake': {                           # пробуждение futex
        'weight': 0.06, 
        'negative': True,
        'bounds': (0.0, 3000)
        },           
    'futex requeue': {                        # перемещение очереди futex
        'weight': 0.06, 
        'negative': True,
        'bounds': (0.0, 1500)
        },       
    
    # ========== СОБЫТИЯ (15%) ==========
    'epoll wait': {                           # ожидание epoll
        'weight': 0.08, 
        'negative': False,
        'bounds': (0.0, 3000000)
        },          
    'epoll ctl': {                            # управление epoll
        'weight': 0.07, 
        'negative': False,
        'bounds': (0.0, 5000000)
        },         
    
    # ========== СИГНАЛЫ (10%) ==========
    'lat_sig install': {                      # установка обработчиков сигналов
        'weight': 0.04, 
        'negative': True,
        'bounds': (0.0, 2000)
        },  
    'lat_sig catch': {                        # перехват сигналов
        'weight': 0.06, 
        'negative': True,
        'bounds': (0.0, 5000)
        },     
}


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
DEFAULT_DISK = 'sda'
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
    # ========== СИСТЕМНЫЕ ВЫЗОВЫ (24%) ==========
    'syscall': {                              # базовые системные вызовы
        'weight': 0.11, 
        'negative': False,
        'bounds': (0.0, 7000000)
        },              
    
    'lat_syscall null': {                     # нулевой syscall - чистая задержка
        'weight': 0.06, 
        'negative': True,
        'bounds': (0.0, 150)
        },     
    'lat_syscall read': {                     # чтение - частая операция
        'weight': 0.05, 
        'negative': True,
        'bounds': (0.0, 150)
        },     
    'lat_syscall write': {                    # запись - частая операция
        'weight': 0.04, 
        'negative': True,
        'bounds': (0.0, 150)
        },    
    
    # ========== ПЛАНИРОВЩИК (26%) ==========
    'lat_ctx -s 0 2 4 8 16 24 32 64 128': {   # переключение контекста
        'weight': 0.08, 
        'negative': True,
        'bounds': (0.0, 20000)
    },
    'sched pipe': {                           # pipe через планировщик
        'weight': 0.09, 
        'negative': True,
        'bounds': (0.0, 1500)
    },
    'sched messaging': {                      # IPC через планировщик
        'weight': 0.09, 
        'negative': True,
        'bounds': (0.0, 100)
    },
    
    # ========== СИНХРОНИЗАЦИЯ (22%) ==========           
    'futex hash': {                           # хэш-таблица с futex
        'weight': 0.08, 
        'negative': False,
        'bounds': (0.0, 40000000)
    },
    'futex wake': {                           # пробуждение futex
        'weight': 0.07, 
        'negative': True,
        'bounds': (0.0, 1500)
        },           
    'futex requeue': {                        # перемещение очереди futex
        'weight': 0.07, 
        'negative': True,
        'bounds': (0.0, 1500)
        },       
    
    # ========== СОБЫТИЯ (16%) ==========
    'epoll wait': {                           # ожидание epoll
        'weight': 0.09, 
        'negative': False,
        'bounds': (0.0, 5000000)
        },          
    'epoll ctl': {                            # управление epoll
        'weight': 0.07, 
        'negative': False,
        'bounds': (0.0, 10000000)
        },         
    
    # ========== СИГНАЛЫ (10%) ==========
    'lat_sig install': {                      # установка обработчиков сигналов
        'weight': 0.04, 
        'negative': True,
        'bounds': (0.0, 200)
        },  
    'lat_sig catch': {                        # перехват сигналов
        'weight': 0.06, 
        'negative': True,
        'bounds': (0.0, 500)
        },     
}

PROCESSES_IPC_CRITERIONS = {
    # ========== СОЗДАНИЕ ПРОЦЕССОВ (35%) ==========
    'spawn': {                                # создание процессов
        'weight': 0.12,
        'negative': False,
        'bounds': (0.0, 800000)            
    },
    'execl': {                                # запуск программ
        'weight': 0.12,
        'negative': False,
        'bounds': (0.0, 600000)              
    },
    'lat_proc fork': {                        # время fork
        'weight': 0.09,
        'negative': True,
        'bounds': (0.0, 5000)                 
    },
    'lat_proc exec': {                        # время exec
        'weight': 0.08,
        'negative': True,
        'bounds': (0.0, 5000)                 
    },
    
    # ========== IPC МЕХАНИЗМЫ (65%) ==========
    'pipe': {                                 # пропускная способность pipe
        'weight': 0.16,
        'negative': False,
        'bounds': (0.0, 90000000)             
    },
    'context1': {                             # контекст pipe-based
        'weight': 0.14,
        'negative': False,
        'bounds': (0.0, 10000000)              
    },
    'lat_pipe': {                             # задержка pipe
        'weight': 0.11,
        'negative': True,
        'bounds': (0.0, 500)                  
    },
    'bw_pipe': {                              # пропускная способность pipe
        'weight': 0.18,
        'negative': False,
        'bounds': (0.0, 50000)                
    },
}

FILESYSTEM_CRITERIONS = {
    # ========== ПРОПУСКНАЯ СПОСОБНОСТЬ (12%) ==========
    'bw_file_rd': {                           # пропускная способность чтения файла
        'weight': 0.12,
        'negative': False,
        'bounds': (0.0, 70000)                
    },

    # ========== ОПЕРАЦИИ КОПИРОВАНИЯ (20%) ==========
    'fstime': {                               # копирование 1024
        'weight': 0.07,
        'negative': False,
        'bounds': (0.0, 60000000)            
    },
    'fsbuffer': {                             # копирование 256
        'weight': 0.06,
        'negative': False,
        'bounds': (0.0, 30000000)            
    },
    'fsdisk': {                               # копирование 4096
        'weight': 0.07,
        'negative': False,
        'bounds': (0.0, 140000000)             
    },
    
    # ========== ЛАТЕНТНОСТЬ ФС (13%) ==========
    'lat_fs 0K': {                            # латентность для малых файлов
        'weight': 0.07,
        'negative': False,                   
        'bounds': (0.0, 500000)               
    },
    'lat_fs 10K': {                           # латентность для 10KB файлов
        'weight': 0.06,
        'negative': False,
        'bounds': (0.0, 500000)              
    },
    
    # ========== FS_MARK НАГРУЗКИ (55%) ==========
    'speed': {                                # общая скорость операций
        'weight': 0.12,
        'negative': False,
        'bounds': (0.0, 500000)              
    },
    'app_overhead': {                         # накладные расходы
        'weight': 0.04, 
        'negative': True, 
        'bounds': (0.0, 850000000)
    },
    'create_avg': {                           # создание файлов
        'weight': 0.10,
        'negative': True,
        'bounds': (0.0, 5000)                
    },
    'write_avg': {                            # запись в файлы
        'weight': 0.08,
        'negative': True,
        'bounds': (0.0, 1500)                
    },
    'fsync_avg': {                            # синхронизация файлов
        'weight': 0.06,
        'negative': True,
        'bounds': (0.0, 7000)                
    },
    'close_avg': {                            # закрытие файлов
        'weight': 0.05, 
        'negative': True, 
        'bounds': (0.0, 150)
    },
    'unlink_avg': {                           # удаление файлов
        'weight': 0.10,
        'negative': True,
        'bounds': (0.0, 5000)                
    }
}

SCRIPTS_CRITERIONS = {
    # ========== SHELL СКРИПТЫ (100%) ==========
    'shell1': {                               # один параллельный скрипт
        'weight': 0.50,
        'negative': False,
        'bounds': (0.0, 500000)               
    },
    'shell8': {                               # восемь параллельных скриптов
        'weight': 0.50,
        'negative': False,
        'bounds': (0.0, 200000)              
    }
}



#################################################################################
# Template                                                                      #
#################################################################################
TOTAL_TEMPLATE = """
╔═══════════════════════════════════════════════════════════════════════════════╗
║                         РЕЗУЛЬТАТЫ ТЕСТИРОВАНИЯ ОС                            ║
╚═══════════════════════════════════════════════════════════════════════════════╝

┌───────────────────────────────────────────────────────────────────────────────┐
│  ИНФОРМАЦИЯ О СИСТЕМЕ                                                         │
├───────────────────────────────────────────────────────────────────────────────┤
│  ОС:          {os_name} {os_version}                                          │
│  Ядро:        {kernel_version}                                                │
│  Процессор:   {cpu_model}                                                     │
│  Ядер/потоков:{cpu_cores}/{cpu_threads}                                       │
│  ОЗУ:         {ram_total}                                                     │
│  Дата теста:  {test_date}                                                     │
│  Хост:        {hostname}                                                      │
└───────────────────────────────────────────────────────────────────────────────┘

┌───────────────────────────────────────────────────────────────────────────────┐
│  ОЦЕНКА ПО ПОДСИСТЕМАМ                                                        │
├───────────────────────────────────────────────────────────────────────────────┤
│  Ядро и системные вызовы         {kernel:>10.1f}
│  Процессы и IPC                  {processes_ipc:>10.1f}
│  Файловая система                {filesystem:>10.1f}
│  Реалистичная нагрузка           {scripts:>10.1f}
└───────────────────────────────────────────────────────────────────────────────┘

┌───────────────────────────────────────────────────────────────────────────────┐
│  ИТОГОВЫЙ РЕЙТИНГ                                                             │
├───────────────────────────────────────────────────────────────────────────────┤
│  Общий рейтинг                    {total:>10.1f}
└───────────────────────────────────────────────────────────────────────────────┘
"""

TOTAL_TEMPLATE_COLOR = """
\033[1;36m╔═══════════════════════════════════════════════════════════════════════════════╗
║                         РЕЗУЛЬТАТЫ ТЕСТИРОВАНИЯ ОС                            ║
╚═══════════════════════════════════════════════════════════════════════════════╝\033[0m

\033[1;34m┌───────────────────────────────────────────────────────────────────────────────┐
│ 🖥️  ИНФОРМАЦИЯ О СИСТЕМЕ                                                       │
├───────────────────────────────────────────────────────────────────────────────┤\033[0m
│  \033[1;37mОС:\033[0m          {os_name} {os_version}
│  \033[1;37mЯдро:\033[0m        {kernel_version}
│  \033[1;37mПроцессор:\033[0m   {cpu_model}
│  \033[1;37mЯдер/потоков:\033[0m{cpu_cores}/{cpu_threads}
│  \033[1;37mОЗУ:\033[0m         {ram_total}
│  \033[1;37mДата теста:\033[0m  {test_date}
│  \033[1;37mХост:\033[0m        {hostname}
\033[1;34m└───────────────────────────────────────────────────────────────────────────────┘\033[0m

\033[1;33m┌───────────────────────────────────────────────────────────────────────────────┐
│ 📊 ОЦЕНКА ПО ПОДСИСТЕМАМ                                                      │
├───────────────────────────────────────────────────────────────────────────────┤\033[0m
│  Ядро и системные вызовы         \033[1;32m{kernel:>10.1f}\033[0m
│  Процессы и IPC                  \033[1;32m{processes_ipc:>10.1f}\033[0m
│  Файловая система                \033[1;32m{filesystem:>10.1f}\033[0m
│  Реалистичная нагрузка           \033[1;32m{scripts:>10.1f}\033[0m
\033[1;33m└───────────────────────────────────────────────────────────────────────────────┘\033[0m

\033[1;36m┌───────────────────────────────────────────────────────────────────────────────┐
│ 📑 ИТОГОВЫЙ РЕЙТИНГ                                                           │
├───────────────────────────────────────────────────────────────────────────────┤\033[0m
│  Общий рейтинг                    \033[1;35m{total:>10.1f}\033[0m
\033[1;36m└───────────────────────────────────────────────────────────────────────────────┘\033[0m
"""

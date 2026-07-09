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
    # ========== СИСТЕМНЫЕ ВЫЗОВЫ (28%) ==========
    'syscall': {                              # базовые системные вызовы
        'weight': 0.15, 
        'negative': False,
        'bounds': (0.0, 7000000),
        'reference': 690000.0
        },              
    
    'lat_syscall null': {                     # нулевой syscall - чистая задержка
        'weight': 0.06, 
        'negative': True,
        'bounds': (0.0, 150),
        'reference': 0.300
        },     
    'lat_syscall read': {                     # чтение - частая операция
        'weight': 0.05, 
        'negative': True,
        'bounds': (0.0, 150),
        'reference': 0.420
        },     
    'lat_syscall write': {                    # запись - частая операция
        'weight': 0.04, 
        'negative': True,
        'bounds': (0.0, 150),
        'reference': 0.390
        },    
    
    # ========== ПЛАНИРОВЩИК (25%) ==========
    'lat_ctx -s 0 2 4 8 16 24 32 64 128': {   # переключение контекста
        'weight': 0.07, 
        'negative': True,
        'bounds': (0.0, 20000),
        'reference': 128.0
    },
    'sched pipe': {                           # pipe через планировщик
        'weight': 0.09, 
        'negative': True,
        'bounds': (0.0, 1500),
        'reference': 18.0
    },
    'sched messaging': {                      # IPC через планировщик
        'weight': 0.09, 
        'negative': True,
        'bounds': (0.0, 100),
        'reference': 0.050
    },
    
    # ========== СИНХРОНИЗАЦИЯ (19%) ==========           
    'futex hash': {                           # хэш-таблица с futex
        'weight': 0.08, 
        'negative': False,
        'bounds': (0.0, 40000000),
        'reference': 1800000.0
    },
    'futex wake': {                           # пробуждение futex
        'weight': 0.07, 
        'negative': True,
        'bounds': (0.0, 1500),
        'reference': 65.0
        },           
    'futex requeue': {                        # перемещение очереди futex
        'weight': 0.04, 
        'negative': True,
        'bounds': (0.0, 1500),
        'reference': 70.0
        },       
    
    # ========== СОБЫТИЯ (16%) ==========
    'epoll wait': {                           # ожидание epoll
        'weight': 0.09, 
        'negative': False,
        'bounds': (0.0, 5000000),
        'reference': 70000.0
        },          
    'epoll ctl': {                            # управление epoll
        'weight': 0.07, 
        'negative': False,
        'bounds': (0.0, 10000000),
        'reference': 375000.0
        },         
    
    # ========== СИГНАЛЫ (10%) ==========
    'lat_sig install': {                      # установка обработчиков сигналов
        'weight': 0.04, 
        'negative': True,
        'bounds': (0.0, 200),
        'reference': 0.365
        },  
    'lat_sig catch': {                        # перехват сигналов
        'weight': 0.06, 
        'negative': True,
        'bounds': (0.0, 500),
        'reference': 1.600
        },     
}

PROCESSES_IPC_CRITERIONS = {
    # ========== СОЗДАНИЕ ПРОЦЕССОВ (35%) ==========
    'spawn': {                                # создание процессов
        'weight': 0.12,
        'negative': False,
        'bounds': (0.0, 800000),
        'reference': 33000.0            
    },
    'execl': {                                # запуск программ
        'weight': 0.12,
        'negative': False,
        'bounds': (0.0, 600000),
        'reference': 15000.0              
    },
    'lat_proc fork': {                        # время fork
        'weight': 0.09,
        'negative': True,
        'bounds': (0.0, 5000),
        'reference': 340.0                 
    },
    'lat_proc exec': {                        # время exec
        'weight': 0.08,
        'negative': True,
        'bounds': (0.0, 5000),
        'reference': 340.0                 
    },
    
    # ========== IPC МЕХАНИЗМЫ (65%) ==========
    'pipe': {                                 # пропускная способность pipe
        'weight': 0.16,
        'negative': False,
        'bounds': (0.0, 90000000),
        'reference': 6500000.0             
    },
    'context1': {                             # контекст pipe-based
        'weight': 0.14,
        'negative': False,
        'bounds': (0.0, 10000000),
        'reference': 650000.0              
    },
    'lat_pipe': {                             # задержка pipe
        'weight': 0.11,
        'negative': True,
        'bounds': (0.0, 500),
        'reference': 18.0                  
    },
    'bw_pipe': {                              # пропускная способность pipe
        'weight': 0.18,
        'negative': False,
        'bounds': (0.0, 50000),
        'reference': 2670.0                
    },
}

FILESYSTEM_CRITERIONS = {
    # ========== ПРОПУСКНАЯ СПОСОБНОСТЬ (12%) ==========
    'bw_file_rd': {                           # пропускная способность чтения файла
        'weight': 0.12,
        'negative': False,
        'bounds': (0.0, 70000),
        'reference': 5500.0                
    },

    # ========== ОПЕРАЦИИ КОПИРОВАНИЯ (20%) ==========
    'fstime': {                               # копирование 1024
        'weight': 0.07,
        'negative': False,
        'bounds': (0.0, 60000000),
        'reference': 4500000.0            
    },
    'fsbuffer': {                             # копирование 256
        'weight': 0.06,
        'negative': False,
        'bounds': (0.0, 30000000),
        'reference': 1250000.0            
    },
    'fsdisk': {                               # копирование 4096
        'weight': 0.07,
        'negative': False,
        'bounds': (0.0, 140000000),
        'reference': 11300000.0             
    },
    
    # ========== ЛАТЕНТНОСТЬ ФС (13%) ==========
    'lat_fs 0K': {                            # латентность для малых файлов
        'weight': 0.07,
        'negative': False,                   
        'bounds': (0.0, 500000),
        'reference': 40000.0               
    },
    'lat_fs 10K': {                           # латентность для 10KB файлов
        'weight': 0.06,
        'negative': False,
        'bounds': (0.0, 500000),
        'reference': 40000.0              
    },
    
    # ========== FS_MARK НАГРУЗКИ (55%) ==========
    'speed': {                                # общая скорость операций
        'weight': 0.12,
        'negative': False,
        'bounds': (0.0, 500000),
        'reference': 32000.0              
    },
    'app_overhead': {                         # накладные расходы
        'weight': 0.04, 
        'negative': True, 
        'bounds': (0.0, 850000000),
        'reference': 40000000.0
    },
    'create_avg': {                           # создание файлов
        'weight': 0.10,
        'negative': True,
        'bounds': (0.0, 5000),
        'reference': 140.0                
    },
    'write_avg': {                            # запись в файлы
        'weight': 0.08,
        'negative': True,
        'bounds': (0.0, 1500),
        'reference': 10.0                
    },
    'fsync_avg': {                            # синхронизация файлов
        'weight': 0.06,
        'negative': True,
        'bounds': (0.0, 7000),
        'reference': 550.0                
    },
    'close_avg': {                            # закрытие файлов
        'weight': 0.05, 
        'negative': True, 
        'bounds': (0.0, 150),
        'reference': 4.0
    },
    'unlink_avg': {                           # удаление файлов
        'weight': 0.10,
        'negative': True,
        'bounds': (0.0, 5000),
        'reference': 280.0                
    }
}

SCRIPTS_CRITERIONS = {
    # ========== SHELL СКРИПТЫ (100%) ==========
    'shell1': {                               # один параллельный скрипт
        'weight': 0.50,
        'negative': False,
        'bounds': (0.0, 500000),
        'reference': 40000.0               
    },
    'shell8': {                               # восемь параллельных скриптов
        'weight': 0.50,
        'negative': False,
        'bounds': (0.0, 200000),
        'reference': 12000.0              
    }
}



#################################################################################
# Template                                                                      #
#################################################################################
OSBENCH_LOGO = """

╔═══════════════════════════════════════════════════════════════╗
║   ██████╗ ███████╗██████╗ ███████╗███╗   ██╗ ██████╗██╗  ██╗  ║
║  ██╔═══██╗██╔════╝██╔══██╗██╔════╝████╗  ██║██╔════╝██║  ██║  ║
║  ██║   ██║███████╗██████╔╝█████╗  ██╔██╗ ██║██║     ███████║  ║
║  ██║   ██║╚════██║██╔══██╗██╔══╝  ██║╚██╗██║██║     ██╔══██║  ║
║  ╚██████╔╝███████║██████╔╝███████╗██║ ╚████║╚██████╗██║  ██║  ║
║   ╚═════╝ ╚══════╝╚═════╝ ╚══════╝╚═╝  ╚═══╝ ╚═════╝╚═╝  ╚═╝  ║
║                OS Performance Testing Suite                   ║
╚═══════════════════════════════════════════════════════════════╝
"""

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
│  ИТОГОВЫЙ ИНДЕКС                                                              │
├───────────────────────────────────────────────────────────────────────────────┤
│  Общий индекс                    {total:>10.1f}
└───────────────────────────────────────────────────────────────────────────────┘
"""

TOTAL_TEMPLATE_COLOR = """

╔═══════════════════════════════════════════════════════════════╗
║   ██████╗ ███████╗██████╗ ███████╗███╗   ██╗ ██████╗██╗  ██╗  ║
║  ██╔═══██╗██╔════╝██╔══██╗██╔════╝████╗  ██║██╔════╝██║  ██║  ║
║  ██║   ██║███████╗██████╔╝█████╗  ██╔██╗ ██║██║     ███████║  ║
║  ██║   ██║╚════██║██╔══██╗██╔══╝  ██║╚██╗██║██║     ██╔══██║  ║
║  ╚██████╔╝███████║██████╔╝███████╗██║ ╚████║╚██████╗██║  ██║  ║
║   ╚═════╝ ╚══════╝╚═════╝ ╚══════╝╚═╝  ╚═══╝ ╚═════╝╚═╝  ╚═╝  ║
║                OS Performance Testing Suite                   ║
╚═══════════════════════════════════════════════════════════════╝

┌────────────────────────────────────────────────────────────────────────────────────────┐
│  ИНФОРМАЦИЯ О СИСТЕМЕ                                                                  │
├────────────────────────────────────────────────────────────────────────────────────────┤
│  \033[1;37mОС:\033[0m               {os_name} {os_version}
│  \033[1;37mЯдро:\033[0m             {kernel_version}
│  \033[1;37mПроцессор:\033[0m        {cpu_model}
│  \033[1;37mЯдер/потоков:\033[0m     {cpu_cores}/{cpu_threads}
│  \033[1;37mОЗУ:\033[0m              {ram_total}
│  \033[1;37mХост:\033[0m             {hostname}
├────────────────────────────────────────────────────────────────────────────────────────┤
│  \033[1;37mДата тестирования:\033[0m{test_date}
│  \033[1;37mВремя выполнения:\033[0m {test_time}
└────────────────────────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────────────────────────┐
│  Ядро и системные вызовы                                                               │
├────────────────────────────────────────────────────────────────────────────────────────┤
│\033[1;33m  {test:<25} {source:>1} {guideline:>22} {result:>13} {ratio:>12} \033[0m    │
├────────────────────────────────────────────────────────────────────────────────────────┤
│  syscall                 UnixBench         {syscall_guideline:>12.1f}    {syscall_result:>10.1f}    {syscall_ratio:>9.3f}  
│  lat_syscall null         LMbench          {lat_syscall_null_guideline:>12.4f}    {lat_syscall_null_result:>10.4f}    {lat_syscall_null_ratio:>9.3f}  
│  lat_syscall read         LMbench          {lat_syscall_read_guideline:>12.4f}    {lat_syscall_read_result:>10.4f}    {lat_syscall_read_ratio:>9.3f}  
│  lat_syscall write        LMbench          {lat_syscall_write_guideline:>12.4f}    {lat_syscall_write_result:>10.4f}    {lat_syscall_write_ratio:>9.3f}  
│  lat_ctx                  LMbench          {lat_ctx_guideline:>12.1f}    {lat_ctx_result:>10.1f}    {lat_ctx_ratio:>9.3f}  
│  sched pipe              Perf Bench        {sched_pipe_guideline:>12.1f}    {sched_pipe_result:>10.3f}    {sched_pipe_ratio:>9.3f}  
│  sched messaging         Perf Bench        {sched_messaging_guideline:>12.3f}    {sched_messaging_result:>10.3f}    {sched_messaging_ratio:>9.3f}  
│  futex hash              Perf Bench        {futex_hash_guideline:>12.0f}    {futex_hash_result:>10.0f}    {futex_hash_ratio:>9.3f}  
│  futex wake              Perf Bench        {futex_wake_guideline:>12.1f}    {futex_wake_result:>10.1f}    {futex_wake_ratio:>9.3f}  
│  futex requeue           Perf Bench        {futex_requeue_guideline:>12.1f}    {futex_requeue_result:>10.1f}    {futex_requeue_ratio:>9.3f}  
│  epoll wait              Perf Bench        {epoll_wait_guideline:>12.0f}    {epoll_wait_result:>10.0f}    {epoll_wait_ratio:>9.3f}  
│  epoll ctl               Perf Bench        {epoll_ctl_guideline:>12.0f}    {epoll_ctl_result:>10.0f}    {epoll_ctl_ratio:>9.3f}  
│  lat_sig install          LMbench          {lat_sig_install_guideline:>12.4f}    {lat_sig_install_result:>10.4f}    {lat_sig_install_ratio:>9.3f}  
│  lat_sig catch            LMbench          {lat_sig_catch_guideline:>12.4f}    {lat_sig_catch_result:>10.4f}    {lat_sig_catch_ratio:>9.3f}  
├────────────────────────────────────────────────────────────────────────────────────────┤
│  INDEX:\033[1;32m                                                                 {kernel:>10.1f}\033[0m  
└────────────────────────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────────────────────────┐
│  Процессы и межпроцессное взаимодействие                                               │
├────────────────────────────────────────────────────────────────────────────────────────┤
│\033[1;33m  {test:<25} {source:>1} {guideline:>22} {result:>13} {ratio:>12} \033[0m    │
├────────────────────────────────────────────────────────────────────────────────────────┤
│  spawn                   UnixBench         {spawn_guideline:>12.1f}    {spawn_result:>10.1f}    {spawn_ratio:>9.3f}  
│  execl                   UnixBench         {execl_guideline:>12.1f}    {execl_result:>10.1f}    {execl_ratio:>9.3f}  
│  lat_proc fork            LMbench          {lat_proc_fork_guideline:>12.4f}    {lat_proc_fork_result:>10.4f}    {lat_proc_fork_ratio:>9.3f}  
│  lat_proc exec            LMbench          {lat_proc_exec_guideline:>12.4f}    {lat_proc_exec_result:>10.4f}    {lat_proc_exec_ratio:>9.3f}  
│  pipe                    UnixBench         {pipe_guideline:>12.0f}    {pipe_result:>10.0f}    {pipe_ratio:>9.3f}  
│  context1                UnixBench         {context1_guideline:>12.1f}    {context1_result:>10.1f}    {context1_ratio:>9.3f}  
│  lat_pipe                 LMbench          {lat_pipe_guideline:>12.4f}    {lat_pipe_result:>10.4f}    {lat_pipe_ratio:>9.3f}  
│  bw_pipe                  LMbench          {bw_pipe_guideline:>12.2f}    {bw_pipe_result:>10.2f}    {bw_pipe_ratio:>9.3f}  
├────────────────────────────────────────────────────────────────────────────────────────┤
│  INDEX:    \033[1;32m                                                             {processes_ipc:>10.1f}\033[0m  
└────────────────────────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────────────────────────┐
│  Файловая система                                                                      │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ \033[1;33m {test:<25} {source:>1} {guideline:>22} {result:>13} {ratio:>12}  \033[0m   │
├────────────────────────────────────────────────────────────────────────────────────────┤
│  bw_file_rd               LMbench          {bw_file_rd_guideline:>12.2f}    {bw_file_rd_result:>10.2f}    {bw_file_rd_ratio:>9.3f}  
│  fstime                  UnixBench         {fstime_guideline:>12.0f}    {fstime_result:>10.0f}    {fstime_ratio:>9.3f}  
│  fsbuffer                UnixBench         {fsbuffer_guideline:>12.0f}    {fsbuffer_result:>10.0f}    {fsbuffer_ratio:>9.3f}  
│  fsdisk                  UnixBench         {fsdisk_guideline:>12.0f}    {fsdisk_result:>10.0f}    {fsdisk_ratio:>9.3f}  
│  lat_fs 0K                LMbench          {lat_fs_0K_guideline:>12.0f}    {lat_fs_0K_result:>10.0f}    {lat_fs_0K_ratio:>9.3f}  
│  lat_fs 10K               LMbench          {lat_fs_10K_guideline:>12.0f}    {lat_fs_10K_result:>10.0f}    {lat_fs_10K_ratio:>9.3f}  
│  speed                    fs_mark          {speed_guideline:>12.1f}    {speed_result:>10.1f}    {speed_ratio:>9.3f}  
│  app_overhead             fs_mark          {app_overhead_guideline:>12.0f}    {app_overhead_result:>10.0f}    {app_overhead_ratio:>9.3f}  
│  create_avg               fs_mark          {create_avg_guideline:>12.1f}    {create_avg_result:>10.1f}    {create_avg_ratio:>9.3f}  
│  write_avg                fs_mark          {write_avg_guideline:>12.1f}    {write_avg_result:>10.1f}    {write_avg_ratio:>9.3f}  
│  fsync_avg                fs_mark          {fsync_avg_guideline:>12.1f}    {fsync_avg_result:>10.1f}    {fsync_avg_ratio:>9.3f}  
│  close_avg                fs_mark          {close_avg_guideline:>12.1f}    {close_avg_result:>10.1f}    {close_avg_ratio:>9.3f}  
│  unlink_avg               fs_mark          {unlink_avg_guideline:>12.1f}    {unlink_avg_result:>10.1f}    {unlink_avg_ratio:>9.3f}  
├────────────────────────────────────────────────────────────────────────────────────────┤
│  INDEX:        \033[1;32m                                                         {filesystem:>10.1f}\033[0m  
└────────────────────────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────────────────────────┐
│  Реалистичная нагрузка                                                                 │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ \033[1;33m {test:<25} {source:>1} {guideline:>22} {result:>13} {ratio:>12}  \033[0m   │
├────────────────────────────────────────────────────────────────────────────────────────┤
│  shell1                  UnixBench         {shell1_guideline:>12.1f}    {shell1_result:>10.1f}    {shell1_ratio:>9.3f}  
│  shell8                  UnixBench         {shell8_guideline:>12.1f}    {shell8_result:>10.1f}    {shell8_ratio:>9.3f}  
├────────────────────────────────────────────────────────────────────────────────────────┤
│  INDEX:      \033[1;32m                                                           {scripts:>10.1f}\033[0m  
└────────────────────────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────────────────────────┐
│\033[1;33m  ОЦЕНКА ПО ПОДСИСТЕМАМ                                                             \033[0m    │
├────────────────────────────────────────────────────────────────────────────────────────┤
│  Ядро и системные вызовы                                      \033[1;32m{kernel:>10.1f}\033[0m            
│  Процессы и IPC                                               \033[1;32m{processes_ipc:>10.1f}\033[0m            
│  Файловая система                                             \033[1;32m{filesystem:>10.1f}\033[0m            
│  Реалистичная нагрузка                                        \033[1;32m{scripts:>10.1f}\033[0m            
└────────────────────────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────────────────────────┐
│\033[1;36m  ИТОГОВЫЙ ИНДЕКС                                                                 \033[0m      │
├────────────────────────────────────────────────────────────────────────────────────────┤
│  Общий рейтинг в баллах                                       \033[1;35m{total:>10.1f}\033[0m            
└────────────────────────────────────────────────────────────────────────────────────────┘
"""

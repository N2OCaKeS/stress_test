'''
    astra qa stand
    пригодные ддля проведения тестирования
'''
HOSTS = { 'sudcm': { 'ip': '10.0.0.21',
                     'full_name': 'sudcm.rtfm.rbt',
                     'short_name': 'sudcm',
                     'port': 2021
                     },
          'fidcm': {'ip': '10.0.20.20',
                    'full_name': 'sudcm.rtfm.rbt',
                    'short_name': 'sudcm',
                    'port': 2026
                    },
          'stand1': {'ip': '10.177.5.159',
                     'full_name': 'stand1.stress.rbt',
                     'short_name':'stand1',
                     },
          'stand2': {'ip': '10.177.5.141',
                     'full_name': 'stand2.stress.rbt',
                     'short_name': 'stand2',
                     },
          }

'''
    Основной лог файл в который попадают результаты тестирования
'''
#SCRIPT_DIR = '/media/sf_git/stress_test/file_system_benchmark'
SCRIPT_DIR = '/home/u/git/stress_test/file_system_benchmark'
LOG_FILENAME = 'fsb_log'
LOG_PATH = '{}/{}'.format(SCRIPT_DIR, LOG_FILENAME)
REPORT_FILENAME = 'fsb_report.txt'
REPORT_PATH = '{}/report'.format(SCRIPT_DIR)
TEMPLATE_PATH = '{}/templates'.format(SCRIPT_DIR)

'''
    Количество Inode
'''
INODE_COUNT = '-N 1100000'
'''
    Имя тестового диска
'''
STORAGE_NAME = 'sda'
'''
    Точка монтирования тестового диска
'''
STORAGE_MOUNT_DIR = '/mnt'
'''
    Системный маркер стендовой машины
'''
MACHINE_POSTFIX = 'osse'
'''
    Имя снапшота для восстановления
'''
SNAPSHOT_NAME = '1.7.2.s'

PORT = '7777'
'''
    astra-admin тестовой машины, от имени которого выполняется тест
'''
USER = 'u'
'''
    пароль astra-admin
'''
PASSWORD = '1'

'''
Процентные величины загрузки диска:
    - *START_BORDER* Начальная граница прогона
    - *STEP* Шаг прогона
    - *END_BORDER* Конечная граница прогона
    
    - TIMEOUT Продолжительность временного теста в секундах
    - NUMBER_OF_TEST_FILES Количество тестовых структур
'''
START_BORDER_FOR_DATA = 10000
STEP_FOR_BORDER = 10000
END_BORDER_FOR_DATA = 1000000

FILES = 10
FILES_STEP = 10
FILES_LIMIT = 100

SIZE = 1024
SIZE_STEP = 1024
SIZE_LIMIT = 10240

TIMEOUT = 3600
NUMBER_OF_TEST_FILES = 5000
'''
    Приставка TH_ обозначает величины для многопоточных тестов.    
'''
TH_START_BORDER_FOR_DATA = 15
TH_STEP_FOR_BORDER = 5
TH_END_BORDER_FOR_DATA = 25
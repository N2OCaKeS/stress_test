'''
    astra qa stand
    Все машины стенда, пригодные ддля проведения тестирования
'''
hosts = { 'sudcm': { 'ip': '10.0.0.21',
                     'full_name': 'sudcm.rtfm.rbt',
                     'short_name': 'sudcm',
                     'port': 2021
                     },
          'sufs': { 'ip': '10.0.0.22',
                    'full_name': 'sufs.rtfm.rbt',
                    'short_name': 'sufs',
                    'port': 2022
                    },
          'susrv': { 'ip': '10.0.0.23',
                     'full_name': 'susrv.rtfm.rbt',
                     'short_name': 'susrv',
                     'port': 2023
                     },
          'sudcs': {'ip': '10.0.0.24',
                    'full_name': 'sudcs.rtfm.rbt',
                    'short_name': 'sudcs',
                    'port': 2024
                    },
          'suac': {'ip': '10.0.0.25',
                   'full_name': 'suac.rtfm.rbt',
                   'short_name': 'suac',
                   'port': 2025
                   },
          'fidcm': {'ip': '10.0.20.20',
                    'full_name': 'sudcm.ipa.rbt',
                    'short_name': 'sudcm',
                    'port': 2026
                    },
          'fidcr1': {'ip': '10.0.20.21',
                     'full_name': 'sudcr1.ipa.rbt',
                     'short_name': 'sudcr1',
                     'port': 2027
                     },
          'fidcr2': {'ip': '10.0.20.22',
                     'full_name': 'sudcr2.ipa.rbt',
                     'short_name': 'sudcr2',
                     'port': 2028
                     },
          'fisrv': {'ip': '10.0.20.23',
                    'full_name': 'susrv.ipa.rbt',
                    'short_name': 'susrv',
                    'port': 2029
                    },
          'fiac': {'ip': '10.0.20.30',
                   'full_name': 'suac.ipa.rbt',
                   'short_name': 'suac',
                   'port': 2030
                   },
          }

'''
    Основной лог файл в который попадают результаты тестирования
'''
LOG_FILENAME = 'fsb_report.txt'
MAIN_DIR = '/media/sf_git/skts-test/testlink/file_system_benchmark'
LOG_PATH = '{}/{}'.format(MAIN_DIR, LOG_FILENAME)
'''
    Количество Inode
'''
INODE_COUNT = '-N 1100000'
'''
    Имя тестового диска
'''
STORAGE_NAME = 'sdb'
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
SNAPSHOT_NAME = '1.7-testing-1.7.2.5 (Smolensk)'

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
START_BORDER_FOR_DATA = 10
STEP_FOR_BORDER = 5
END_BORDER_FOR_DATA = 100
TIMEOUT = 3600
NUMBER_OF_TEST_FILES = 5000
'''
    Приставка TH_ обозначает величины для многопоточных тестов.    
'''
TH_START_BORDER_FOR_DATA = 15
TH_STEP_FOR_BORDER = 5
TH_END_BORDER_FOR_DATA = 25
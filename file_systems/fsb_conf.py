import requests
jira_url_api = 'http://bendiks.devos.astralinux.ru/rest/api/get-jira-url'
confluence_url_api = 'http://bendiks.devos.astralinux.ru/rest/api/get-confluence-url'
response_jira_url = requests.get(jira_url_api)
response_confluence_url = requests.get(confluence_url_api)
JIRA_URL = response_jira_url.text
CONFLUENCE_URL = response_confluence_url.text


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
SCRIPT_DIR = '/home/u/git/stress_test/file_systems'
LOG_FILENAME = 'fsb_log'
LOG_PATH = '{}/{}'.format(SCRIPT_DIR, LOG_FILENAME)
REPORT_FILENAME = 'fsb_report.txt'
REPORT_PATH = '{}/report'.format(SCRIPT_DIR)
TEMPLATE_PATH = '{}/templates'.format(SCRIPT_DIR)
INFO_FILENAME = 'fsb_info.txt'
VENV_PATH = '/home/u/python/Python-3.12.1/venv/bin/python3.12'

PACKAGES = {'ext2': 'e2fsprogs',
            'ext3': 'e2fsprogs',
            'ext4': 'e2fsprogs',
            'fat': 'dosfstools',
            'ntfs': 'ntfs-3g',
            'xfs': 'xfsprogs',
            'exfat': 'exfat-utils'
}

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

FILES = 10000
FILES_STEP = 10000
FILES_LIMIT = 100000

#Для EXT4 Stand4
FILES_ST4 = 10000
FILES_STEP_ST4 = 5000
FILES_LIMIT_ST4 = 100000

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

GRAPH_DESCRIPTIONS = {
    'fsb_file_count_app_overhead_graph.png': '<p style="font-family: Century Gothic, sans-serif; font-size: 14px;">'
                                             'График зависимости времененных затрат приложения (не считая системные вызовы) от количества файлов.'
                                             '<ul>'
                                             '    <li><b>OX</b>: Количество тестовых файлов;</li>'
                                             '    <li><b>OY</b>: Времененные затраты приложения (мсек);</li>'
                                             '    <li><b>Функция</b>: Аппроксимирующая функция точек;</li>'
                                             '</ul></p>',

    'fsb_file_count_speed_graph.png': '<p style="font-family: Century Gothic, sans-serif; font-size: 14px;">'
                                      'График зависимости скорости записи файлов на диск от количества файлов.'
                                      '<ul>'
                                      '    <li><b>OX</b>: Количество тестовых файлов;</li>'
                                      '    <li><b>OY</b>: скорость записи файлов на диск (мсек);</li>'
                                      '    <li><b>Функция</b>: Аппроксимирующая функция точек;</li>'
                                      '</ul></p>',

    'fsb_file_count_create_graph.png': '<p style="font-family: Century Gothic, sans-serif; font-size: 14px;">'
                                       'График зависимости мининмальных/средних/максимальных времязатрат на обработку системного вызова от количества файлов. Системный вызов CREATE.'
                                       '<ul>'
                                       '    <li><b>OX</b>: Количество тестовых файлов;</li>'
                                       '    <li><b>OY</b>: Времязатраты на обработку системного вызова (мсек);</li>'
                                       '</ul></p>',

    'fsb_file_count_write_graph.png': '<p style="font-family: Century Gothic, sans-serif; font-size: 14px;">'
                                       'График зависимости мининмальных/средних/максимальных времязатрат на обработку системного вызова от количества файлов. Системный вызов WRITE.'
                                       '<ul>'
                                       '    <li><b>OX</b>: Количество тестовых файлов;</li>'
                                       '    <li><b>OY</b>: Времязатраты на обработку системного вызова (мсек);</li>'
                                       '</ul></p>',
    'fsb_file_count_fsync_graph.png': '<p style="font-family: Century Gothic, sans-serif; font-size: 14px;">'
                                       'График зависимости мининмальных/средних/максимальных времязатрат на обработку системного вызова от количества файлов. Системный вызов FSYNC.'
                                       '<ul>'
                                       '    <li><b>OX</b>: Количество тестовых файлов;</li>'
                                       '    <li><b>OY</b>: Времязатраты на обработку системного вызова (мсек);</li>'
                                       '</ul></p>',
    'fsb_file_count_sync_graph.png': '<p style="font-family: Century Gothic, sans-serif; font-size: 14px;">'
                                       'График зависимости мининмальных/средних/максимальных времязатрат на обработку системного вызова от количества файлов. Системный вызов SYNC.'
                                       '<ul>'
                                       '    <li><b>OX</b>: Количество тестовых файлов;</li>'
                                       '    <li><b>OY</b>: Времязатраты на обработку системного вызова (мсек);</li>'
                                       '</ul></p>',
    'fsb_file_count_close_graph.png': '<p style="font-family: Century Gothic, sans-serif; font-size: 14px;">'
                                       'График зависимости мининмальных/средних/максимальных времязатрат на обработку системного вызова от количества файлов. Системный вызов CLOSE.'
                                       '<ul>'
                                       '    <li><b>OX</b>: Количество тестовых файлов;</li>'
                                       '    <li><b>OY</b>: Времязатраты на обработку системного вызова (мсек);</li>'
                                       '</ul></p>',
    'fsb_file_count_unlink_graph.png': '<p style="font-family: Century Gothic, sans-serif; font-size: 14px;">'
                                       'График зависимости мининмальных/средних/максимальных времязатрат на обработку системного вызова от количества файлов. Системный вызов UNLINK.'
                                       '<ul>'
                                       '    <li><b>OX</b>: Количество тестовых файлов;</li>'
                                       '    <li><b>OY</b>: Времязатраты на обработку системного вызова (мсек);</li>'
                                       '</ul></p>',
}
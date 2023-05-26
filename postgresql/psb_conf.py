SCRIPT_DIR = '/home/u/git/stress_test/postgresql'
REPORT_PATH = '{}/report'.format(SCRIPT_DIR)
#LOG_FILENAME = f'{SCRIPT_DIR}/psb_log'
LOG_FILENAME = f'{REPORT_PATH}/psb.log'
RUN_LOG = f'{REPORT_PATH}/run_psb.log'
REPORT_FILENAME = '{}/psb_report.txt'.format(REPORT_PATH)
REPORT_SYSMON_FILENAME = '{}/psb_sysmon_report.txt'.format(REPORT_PATH)
DATA_SYSMON_FILENAME = '{}/psb_data_sysmon.txt'.format(REPORT_PATH)
TEMPLATE_PATH = '{}/templates'.format(SCRIPT_DIR)
INFO_FILENAME = '{}/psb_info.txt'.format(SCRIPT_DIR)

'''
   Названия скриптов из папки sql.
   *upgrade - скрипт донастройки после pgbech -i
   *transaction - скрипт специальной тестовой транзакции 
'''
MAC_SQL_UPGRADE = 'psb_addmac.sql'
MAC_SQL_TRANSACTION = 'psb_mac_transaction.sql'
MIC_SQL_UPGRADE = ''
MIC_SQL_TRANSACTION = ''
ACL_SQL_UPGRADE = ''
ACL_SQL_TRANSACTION = ''
'''
    Параметры БД
'''
PG_VERSION = 11
STORAGE = 'sda'
DATABASE_NAME = 'mtest'
TABLESPACE_DEFAULT_PATH = '/var/lib/postgresql/'+ str(PG_VERSION) +'/pg_default'
TABLESPACE_DEFAULT = 'pg_default'
TABLESPACE_MAC_PATH = '/pg_default_mac'
TABLESPACE_MAC = 'pg_default_mac'
PG_SETEST_CLUSTER = 'setest_cl'
PG_SETEST_PORT = 6000

'''
    Создать таблицы pgbench_accounts, pgbench_tellers и pgbench_branches с заданным фактором заполнения. 
'''
FILLING_FACTOR = 100
'''
    Умножить число генерируемых строк на заданный коэффициент. 
    Например, с ключом -s 100 в таблицу pgbench_accounts будут записаны 10 000 000 строк. 
    При коэффициенте, равном 20 000 или больше, столбцы, 
    содержащие идентификаторы счетов (столбцы aid), перейдут к большим целым числам (типу bigint),
    чтобы в них могли уместиться все возможные значения идентификаторов.
'''
DEFAULT_SCALE_FACTOR = 500
SCALE_FACTOR = 10  # 1000
SCALE_FACTOR_STEP = 10  # 2000
LIMITE_SCALE_FACTOR = 100  # 100000
'''
    Число транзакций, которые будут выполняться каждым клиентом.
'''
DEFAULT_TRANSACTIONS = 100000
TRANSACTIONS = 10  # 100000
TRANSACTIONS_STEP = 10  # 100000
LIMITE_TRANSACTIONS = 100  # 10000000
'''
    Число потоков
'''
DEFAULT_THREADS = 200
THREADS = 10  # 1000
THREADS_STEP = 10  # 1000
LIMITE_THREADS = 100  # 10000
'''
    Число клиентов
'''
CLIENTS = 1 #100
CLIENTS_STEP = 1 #100
STEP_RATIO_BY_CLIENTS = 1
LIMITE_CLIENTS = 1 #500
'''
    Расширенный репозиторий
'''
EXTREP = 'deb ftp://10.177.5.111/astra/testing/extended-1.7-testing 1.7_x86-64 main contrib non-free astra-ce'

'''
    Описание для графиков отчета
'''
GRAPH_DESCRIPTIONS = {
    'psb_clients_la_graph.png': '<p style="font-family: Century Gothic, sans-serif; font-size: 14px;">График зависимости средней задержки отклика приложения от количества клиентов, одновременно выполняющих транзакции.<ul><li><b>OX</b>: Количество клиентов, одновременно выполняющих транзакции;</li><li><b>OY</b>: Средняя задержка отклика приложения;</li><li><b>Функция</b>: Аппроксимирующая функция точек;</li></ul></p>',
    'psb_clients_tps1_graph.png': '<p style="font-family: Century Gothic, sans-serif; font-size: 14px;">График зависимости числа транзакций в секунду (с учетом установки соединения) от количества клиентов, одновременно выполняющих транзакции.<ul><li><b>OX</b>: Количество клиентов, одновременно выполняющих транзакции;</li><li><b>OY</b>: Транзакций в секунду, включая установление соединений</li><li><b>Функция</b>: Аппроксимирующая функция точек</li></ul></p>',
    'psb_clients_tps2_graph.png': '<p style="font-family: Century Gothic, sans-serif; font-size: 14px;">График зависимости числа транзакций в секунду (без учета установки соединения) от количества клиентов, одновременно выполняющих транзакции.<ul><li><b>OX</b>: Количество клиентов, одновременно выполняющих транзакции;</li><li><b>OY</b>: Транзакций в секунду, не включая установление соединений</li><li><b>Функция</b>: Аппроксимирующая функция точек</li></ul></p>',
    'psb_clients_tpsall_graph.png': '<p style="font-family: Century Gothic, sans-serif; font-size: 14px;">Сравнительный график зависимости числа транзакций в секунду без учета установки соединения и числа транзакций в секунду с учетом установки соединения от количества клиентов, одновременно выполняющих транзакции.<ul><li><b>OX</b>: Количество клиентов, одновременно выполняющих транзакции;</li><li><b>OY</b>: Транзакций в секунду</li><li><b>Функция</b>: Аппроксимирующая функция точек</li></ul></p>',
}

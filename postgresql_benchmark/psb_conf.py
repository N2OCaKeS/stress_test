SCRIPT_DIR = '/media/sf_git/stress_test/postgresql_benchmark'
LOG_FILENAME = '{}/psb_log'.format(SCRIPT_DIR)
REPORT_PATH = '{}/report'.format(SCRIPT_DIR)
REPORT_FILENAME = '{}/psb_report.txt'.format(REPORT_PATH)
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
DATABASE_NAME = 'mtest'
TABLESPACE_DEFAULT_PATH = '/var/lib/postgresql/11/pg_default'
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
SCALE_FACTOR = 10  # 1000
SCALE_FACTOR_STEP = 10  # 2000
LIMITE_SCALE_FACTOR = 100  # 100000
'''
    Число транзакций, которые будут выполняться каждым клиентом.
'''
TRANSACTIONS = 10  # 100000
TRANSACTIONS_STEP = 10  # 100000
LIMITE_TRANSACTIONS = 100  # 10000000
'''
    Число потоков
'''
THREADS = 10  # 1000
THREADS_STEP = 10  # 1000
LIMITE_THREADS = 100  # 10000
'''
    Число клиентов
'''
CLIENTS = 100
CLIENTS_STEP = 100
STEP_RATIO_BY_CLIENTS = 1
LIMITE_CLIENTS = 5000

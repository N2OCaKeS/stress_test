import subprocess
import logging
from sys import exit
from os import chmod, remove, chdir, path, mkdir, linesep
from os.path import exists
from shutil import copy2
import ftplib
import requests
from psb_conf import SCRIPT_DIR, DATABASE_NAME, REPORT_PATH, LOG_FILENAME
import pysnooper
import numpy as np


if not path.isdir(REPORT_PATH):
    mkdir(REPORT_PATH)

logging.basicConfig(
        filename=LOG_FILENAME, 
        level=logging.INFO,
        filemode='a',
        format='%(asctime)s - %(levelname)s - %(name)s - %(funcName)s: %(lineno)d - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
)


def check_output_command(command, out=None):
    result = subprocess.Popen([command], shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    output, errors = result.communicate()
    output = linesep.join([s for s in output.splitlines() if s])
    errors = linesep.join([s for s in errors.splitlines() if s])
    if errors == "":
        return output
    elif out != None:
        return errors + output
    else:
        return errors

def cmd(command, err=subprocess.DEVNULL, out=subprocess.DEVNULL):
    subprocess.run(command, shell=True, stderr=err, stdout=out)


def astra_version():
    version = []
    # astra_digit_version = subprocess.run("cat /etc/os-release | grep '^VERSION_ID'",
    #                                      shell=True,
    #                                      stdout=subprocess.PIPE,
    #                                      stderr=subprocess.DEVNULL).stdout.decode("utf-8")
    # if "2.12" in astra_digit_version:
    #     version.append("2.12")
    # elif "1.6" in astra_digit_version:
    #     version.append("1.6")
    # elif "1.7" in astra_digit_version:
    #     version.append("1.7")
    # elif "4.7" in astra_digit_version:
    #     version.append("4.7")
    # elif "8.1" in astra_digit_version:
    #     version.append("8.1")
    # else:
    #     print("Version of distribution not found")
    #     exit(2)


    if exists("/etc/astra_version"):
        with open("/etc/astra_version", "r") as file:
            astra_update_version = file.read()
        version.append(astra_update_version.strip('\n'))

        try:
            with open("/etc/astra_license", "r") as file:
                astra_license = file.read()
                if "orel" in astra_license:
                    version.append("orel")
                elif "smolensk" in astra_license:
                    version.append("smolensk")
                elif "voronezh" in astra_license:
                    version.append("voronezh")
                else:
                    print("Version of distribution not found")
                    exit(2)
        except IOError:
            with open("/etc/astra_version", "r") as file:
                astra_version = file.read()
                if "1.6" in astra_version:
                    version.append("smolensk")
                elif "1.5" in astra_version:
                    version.append("smolensk")
                elif "8.1" in astra_version:
                    version.append("smolensk")
                elif "2.12" in astra_version:
                    version.append("orel")
                else:
                    print("Version of distribution not found")
                exit(2)
    else:
        if exists("/etc/debian_version"):
            with open("/etc/debian_version", "r") as file:
                debian_version = file.read()
            version.append(debian_version.strip('\n'))
            return (version[0], 'orel')
    return version

#@pysnooper.snoop()
def init_test_tables(database,
                     tablespace,
                     port,
                     t_scale_factor,
                     t_filling_factor,
                     debian=False,
                     parsec=False,
                     tantor=False):

    '''
        pgbench -i создаёт четыре таблицы
        pgbench_accounts, pgbench_branches, pgbench_history и pgbench_tellers
    '''
    if debian == True:
        cmd("su -c 'pgbench -i --tablespace={ts} -s {s} -F {f} {db}' postgres".format(db=database,
                                                                                    ts=tablespace,                                                                                                    
                                                                                    s=t_scale_factor,
                                                                                    f=t_filling_factor))
    elif parsec == True:
        cmd(f"su -c 'pgbench -i -h localhost --macs -p {port} -s {t_scale_factor} -F {t_filling_factor} test_parsec' postgres")
    elif tantor == True:
        cmd(f"/opt/tantor/db/15/bin/pgbench -i -h localhost -s {t_scale_factor} -p 5432 -F {t_filling_factor} -U postgres test_parsec")
    else:
        cmd("su -c 'pgbench -i -h localhost -p {p} --tablespace={ts} -s {s} -F {f} {db}' postgres".format(db=database,
                                                                                                          ts=tablespace,
                                                                                                          p=port,
                                                                                                          s=t_scale_factor,
                                                                                                          f=t_filling_factor))

    
def upgrade_test_table(sql_script):
    '''
        Функция донастройки после "pgbench -i"
    '''
    copy2('{}/sql/{}'.format(SCRIPT_DIR, sql_script), '/tmp/{}'.format(sql_script))
    chmod('/tmp/{}'.format(sql_script), 0o644)
    chdir('/tmp/')
    cmd('su -c "psql -p 5432 -f {}" postgres'.format(sql_script))
    remove('/tmp/{}'.format(sql_script))

#@pysnooper.snoop()
def pgbench(start_cmd):
    '''
        Запуск на стандартных транзакциях
    '''
    test = subprocess.run(start_cmd,
                          shell=True,
                          stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE)
    return [test.stdout.decode("utf-8"), test.stderr.decode("utf-8")]


def pgbench_custom(sql_script, start_cmd):
    '''
        Запуск с указание нестандартных транзанкий в виде скрипта
    '''
    copy2('{}/sql/{}'.format(SCRIPT_DIR, sql_script), '/tmp/{}'.format(sql_script))
    chmod('/tmp/{}'.format(sql_script), 0o644)
    chdir('/tmp/')
    test = subprocess.run(start_cmd,
                          shell=True,
                          stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE)
    remove('/tmp/{}'.format(sql_script))
    return [test.stdout.decode("utf-8"), test.stderr.decode("utf-8")]


def get_memory_load_by_psql():
    qty_memory = subprocess.run("ps -FC postgres  | awk {'print $6'} | awk 'NR!=1' | awk '{ sum += $1 } END { print sum }'",
                                         shell=True,
                                         stdout=subprocess.PIPE,
                                         stderr=subprocess.DEVNULL).stdout.decode("utf-8") # в байтах
    if qty_memory != "":
        qty_memory = float(qty_memory)
        with open('/proc/meminfo', 'r') as procfile:
            mem_total = int(procfile.readline().replace(" ", "")[:-3].split(":")[1]) * 1024
            load_psql_memory = round(qty_memory * 100 / mem_total, 2)
    else:
        load_psql_memory = 0

    return load_psql_memory


def perf():
    logging.info(subprocess.run('ls -lh', shell=True))
    logging.info(subprocess.run('sudo perf script -i perf.data > out.perf1', shell=True))
    logging.info(subprocess.run('sudo perl libs/libstackcollapse-perf.pl out.perf1 > out.folded1', shell=True))
    logging.info(subprocess.run(f'sudo perl libs/libflamegraph.pl out.folded1 > {REPORT_PATH}/result_flamegraph.svg', shell=True))


def dump():
    logging.info(subprocess.run(f'pg_dump -U postgres -d postgres -F tar -f {REPORT_PATH}/dump_db.tar', shell=True))


def log_in(name, message):
    logging.info(name)
    logging.info(message)
    logging.info('-----' * 20)

def upload_results_to_ftp(rc_name, path_to_file, file_name):
    ftp = ftplib.FTP('10.177.103.10')
    ftp.login()
    ftp.cwd('stress_test')
    try:
        ftp.mkd(rc_name)
    except ftplib.error_perm:
        pass
    #ftp.sendcmd('SITE CHMOD 777 ' + rc_name)
    ftp.cwd(rc_name)
    with open(path_to_file, 'rb') as rf:
        ftp.storbinary('STOR ' + file_name, rf)
    ftp.quit()

def response():
    try:
        jira = requests.get('https://jira.astralinux.ru').status_code
        life = requests.get('https://life.astralinux.ru').status_code
        return jira, life
    except Exception as e:
        jira, life = str(type(e).__name__), str(e)
        return jira, life


class BaseTest:
    def __init__(self,
                 database=None,
                 storage_device=None,
                 stand_number=None,
                 prepare=True
                 ):
        
        self.database = database
        self.storage_device = storage_device
        self.stand_number = stand_number
        self.prepare = prepare

    def __cmd(self, command):
        subprocess.run(command, shell=True)

    def test_run(self, clients, repeat):
        repeat_list = [str(clients) for i in range(repeat)]
        repeat_str = ' '.join(repeat_list)
        
        #Создание и настройка БД
        if self.prepare:
            self.__cmd(f'sudo bash default_base_up.sh "{repeat_str}" {self.database} {self.storage_device} {self.stand_number} astra')
        self.__cmd('sudo bash start_test.sh')
        if self.database == 'tantor':
            self.__cmd('cat test/pgbench_result.txt | grep tps | awk \'{print$3}\' >> result_testing.txt')
        else:
            self.__cmd('cat test/pgbench_result.txt | grep including | awk \'{print$3}\' >> result_testing.txt')

        if path.isfile('result_testing.txt'):
            with open('result_testing.txt', 'r') as r:
                raw_results = r.read().split('\n')
                tps_values = np.array([int(float(x)) for x in raw_results if x.replace('.', '', 1).isdigit()])
        else: print('Файл с результатами отсутствует'); exit(1)
        print(f'Общий список всех результатов:\n{tps_values}')


        #Лимит группы по количеству элементов, принимаемой к расчетам, в %
        valid_values_percent = 30 #50
        #Лимит отклонения, в %
        percent_limit = 5 #2 #1.5

        def check_value(value, all_values, percent_limit):
            diffs = np.abs((all_values - value) / value * 100)
            return np.sum(diffs <= percent_limit) >= len(all_values) / 2


        valid_values = [value for value in tps_values if check_value(value, tps_values, percent_limit)]
        novalid_values = [value for value in tps_values if value not in valid_values]
        print('Используемые в расчетах значения:', valid_values)
        print('Отсеянные значения:', novalid_values)

        if len(valid_values) >= len(tps_values) * valid_values_percent / 100:
            mean_cleaned = np.mean(valid_values)
            print(f"Среднее значение без учета аномалий: {int(mean_cleaned)}")
            return int(mean_cleaned)
        else:
            print('Нет подходящих групп значений для расчета среднего')
            return 'NaN'
        

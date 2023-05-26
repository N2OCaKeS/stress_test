#!venv/bin/python
# -*- coding: UTF-8 -*-

# ;===========================================================
# ; Author: rkuznetsov@astralinux.ru
# ; Date: 2022
# ;===========================================================

import subprocess
import logging
from sys import exit
from os import chmod, remove, chdir, path, mkdir, linesep
from shutil import copy2
from psb_conf import SCRIPT_DIR, DATABASE_NAME, REPORT_PATH, LOG_FILENAME


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

    return version


def init_test_tables(database,
                     tablespace,
                     port,
                     t_scale_factor,
                     t_filling_factor):

    '''
        pgbench -i создаёт четыре таблицы
        pgbench_accounts, pgbench_branches, pgbench_history и pgbench_tellers
    '''
    cmd("su -c 'pgbench -i -h localhost -p {p} --tablespace={ts} -s {s} -F {f} {db}' postgres".format(db=database,
                                                                                                      ts=tablespace,
                                                                                                      p=port,
                                                                                                      s=t_scale_factor,
                                                                                                      f=t_filling_factor))

    # TODO: Сделать вывод размера БД


def upgrade_test_table(sql_script):
    '''
        Функция донастройки после "pgbench -i"
    '''
    copy2('{}/sql/{}'.format(SCRIPT_DIR, sql_script), '/tmp/{}'.format(sql_script))
    chmod('/tmp/{}'.format(sql_script), 0o644)
    chdir('/tmp/')
    cmd('su -c "psql -p 5432 -f {}" postgres'.format(sql_script))
    remove('/tmp/{}'.format(sql_script))


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
    logging.info(check_output_command('ls -lh'))
    logging.info(check_output_command('sudo perf script -i perf.data > out.perf1', out=True))
    logging.info(check_output_command('sudo perl libs/libstackcollapse-perf.pl out.perf1 > out.folded1', out=True))
    logging.info(check_output_command(f'sudo perl libs/libflamegraph.pl out.folded1 > {REPORT_PATH}/result_flamegraph.svg', out=True))


def dump():
    logging.info(check_output_command(f'pg_dump -U postgres -d postgres -F tar -f {REPORT_PATH}/dump_db.tar', out=True))


def log_in(name, message):
    logging.info(name)
    logging.info(message)
    logging.info('-----' * 20)


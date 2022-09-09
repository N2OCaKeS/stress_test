#!venv/bin/python
# -*- coding: UTF-8 -*-

# ;===========================================================
# ; Author: rkuznetsov@astralinux.ru
# ; Date: 2022
# ;===========================================================

import subprocess

from sys import exit
from os import chmod, remove, chdir
from shutil import copy2
from psb_conf import SCRIPT_DIR


def cmd(command, err=subprocess.DEVNULL, out=subprocess.DEVNULL):
    subprocess.run(command, shell=True, stderr=err, stdout=out)


def astra_version():
    version = []
    astra_digit_version = subprocess.run("cat /etc/os-release | grep '^VERSION_ID'",
                                         shell=True,
                                         stdout=subprocess.PIPE,
                                         stderr=subprocess.DEVNULL).stdout.decode("utf-8")
    if "2.12" in astra_digit_version:
        version.append("2.12")
    elif "1.6" in astra_digit_version:
        version.append("1.6")
    elif "1.7" in astra_digit_version:
        version.append("1.7")
    elif "4.7" in astra_digit_version:
        version.append("4.7")
    elif "8.1" in astra_digit_version:
        version.append("8.1")
    else:
        print("Version of distribution not found")
        exit(2)
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
                     t_scale_factor,
                     t_filling_factor):

    '''
        pgbench -i создаёт четыре таблицы
        pgbench_accounts, pgbench_branches, pgbench_history и pgbench_tellers
    '''
    cmd("su -c 'pgbench -i -h localhost -p 6000 --tablespace={ts} -s {s} -F {f} {db}' postgres".format(db=database,
                                                                                                       ts=tablespace,
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
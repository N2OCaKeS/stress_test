# -*- coding: utf-8 -*-

# ;===========================================================
# ; Author: ivelikanov@astralinux.ru
# ; Date: 2022
# ;===========================================================

import re
import shutil
import os.path
import argparse
import subprocess
import numpy as np
import pandas as pd
import libs.libtable as libtable
import libs.libscanner as libscanner

from time import sleep
from datetime import datetime
from sklearn import preprocessing
from os import chmod, mkdir, getcwd
from find_err_in_logs import collecting_logs
from libs.libsng import astra_version, check_service_status, get_memory_load_by_syslog, put_system_info_in_file

from sng_conf import SERVICE_COUNT, TIME_EXEC, REPORT_PATH, IMAGE_WIDTH, IMAGE_HEIGHT, INFO_FILENAME


TIME_START_SCRIPT = datetime.now()

DESCRIPTION = ""
parser = argparse.ArgumentParser(description=DESCRIPTION)
parser.add_argument('-ll', '--log_level',
                    action='store',
                    required=False,
                    choices=['debug',
                             'info',
                             'notice',
                             'warn',
                             'err',
                             'crit',
                             'emerg',
                             'crit..emerg',
                             'err..emerg',
                             'warn..emerg',
                             'notice..emerg',
                             'info..emerg',
                             'debug..emerg'],
                    default='debug',
                    help='log level for syslog-ng',
                    dest='LOG_LEVEL')

args = parser.parse_args()

def cmd(command):
    subprocess.run(command,
                   shell=True,
                   stderr=subprocess.DEVNULL)


if __name__ == '__main__':  # TODO: больше комментов!!!

    # Если отстуствует директория для отчета, необходимо создать
    if os.path.exists(REPORT_PATH) is False:
        mkdir(REPORT_PATH)

    '''
        Установка необходимого уровня логирования
    '''
    default_filter = r'(filter\sf_(dbg|debug|info|notice|warn|err(or)?|crit)\s\{\slevel\().+(\).*)'
    new_filter = r'\1{ll}\4'.format(ll=args.LOG_LEVEL)

    with open('/etc/syslog-ng/syslog-ng.conf', 'r') as main_syslog_ng_conf:
        new_config = re.sub(default_filter, new_filter, main_syslog_ng_conf.read())
    with open('/etc/syslog-ng/syslog-ng.conf', 'w') as main_syslog_ng_conf:
        main_syslog_ng_conf.write(new_config)

    '''
        Создание юнит файлов с логерами
    '''
    dir = getcwd()

    for service_num in range(1, SERVICE_COUNT+1):

        service_name = 'dirtylogger{}.service'.format(service_num)
        shutil.copy('sng_service_template.py', '/tmp/dirtylogger{}.py'.format(service_num))
        chmod('/tmp/dirtylogger{}.py'.format(service_num), 0o0777)

        unit = ['[Unit]\n',
                'Description=test service by ivelikanov@astralinux.ru\n',
                'After=multi-user.target\n',
                '[Service]\n',
                'Type=simple\n',
                'Restart=always\n',
                'WorkingDirectory={}/\n'.format(dir),
                'OOMScoreAdjust = -100\n', # Prohibition on the use of the out-of-memory service and the OOM trigger mechanism
                'ExecStart={}/venv/bin/python3 /tmp/dirtylogger{}.py\n'.format(dir, service_num),
                'TimeoutSec=1\n',
                '[Install]\n',
                'WantedBy = multi - user.target\n']

        with open('/etc/systemd/system/{}'.format(service_name), 'w') as test_unit:
            test_unit.writelines(unit)

        sleep(0.01)
        cmd("systemctl start {}".format(service_name))

    data_cpu, data_memory, data_syslog_memory, data_disk, data_time = [], [], [], [], []
    sc = libscanner.Scanner()

    '''
        Добавление нулевых значений для корректного подсчета рейтинга
    '''
    data_cpu.append(0)
    data_memory.append(0)
    data_syslog_memory.append(0)    
    data_disk.append(sc.get_disk_load())
    data_time.append(0)

    time_exec = TIME_EXEC * 60
    qty_sec_after_start = 1
    

    '''
       Сбор данных с CPU, Memory, Disk 
    '''
    while time_exec > 0 and check_service_status('syslog-ng'):
        sleep(0.25)
        data_cpu.append(sc.get_cpu_load())
        data_memory.append(sc.get_memory_load())
        data_syslog_memory.append(get_memory_load_by_syslog())
        data_disk.append(sc.get_disk_load())
        data_time.append(qty_sec_after_start)
        time_exec -= 1
        qty_sec_after_start += 1

    '''
        Остановка сервисов
    '''
    for service_num in range(1, SERVICE_COUNT+1):
         cmd("systemctl stop dirtylogger{}.service".format(service_num))

    '''
        Создание отчета
    '''
    sng_data = pd.DataFrame(data={'load_cpu': data_cpu, 'load_memory': data_memory, 'load_syslog_ng_memory': data_syslog_memory, 'load_disk': data_disk}, index=data_time)
    scaler = preprocessing.MinMaxScaler()
    # Нормализуем данные
    d = scaler.fit_transform(sng_data)
    # Строим новый dataframe с нормированными данными
    scaled_sng_data = pd.DataFrame(d, columns=sng_data.columns)
    
    report = libtable.Report(os.path.expanduser(REPORT_PATH), float(IMAGE_WIDTH), float(IMAGE_HEIGHT))
    rating_cpu = report.get_rating(x=data_time, y=scaled_sng_data['load_cpu'])
    rating_memory = report.get_rating(x=data_time, y=scaled_sng_data['load_memory'])
    rating_syslog_memory = report.get_rating(x=data_time, y=scaled_sng_data['load_syslog_ng_memory'])
    rating_disk = report.get_rating(x=data_time, y=scaled_sng_data['load_disk'])
    total_rating = report.get_total_rating([rating_cpu, rating_memory, rating_syslog_memory, rating_disk])

    # Вывод данных на экран
    print("Total rating:", total_rating)

    '''
        Создание текстового файла с отчетом
    '''
    with open('{}/sng_report.txt'.format(os.path.expanduser(REPORT_PATH)), 'w') as report_txt:
        report_txt.writelines('Astra_version: {}\n'.format(astra_version()[2]))
        report_txt.writelines('Astra_mode: {}\n'.format(astra_version()[1]))
        report_txt.writelines('Kernel: {}\n'.format(astra_version()[3]))
        report_txt.writelines('Service_count: {}\n'.format(SERVICE_COUNT))
        report_txt.writelines('Load_time_execution: {} minutes\n'.format(TIME_EXEC))
        report_txt.writelines('Rating_CPU: {}\n'.format(rating_cpu))
        report_txt.writelines('Rating_memory: {}\n'.format(rating_memory))
        report_txt.writelines('Rating_Syslog-NG_memory: {}\n'.format(rating_syslog_memory))
        report_txt.writelines('Rating_disk: {}\n'.format(rating_disk))
        report_txt.writelines('Total_rating: {}\n'.format(total_rating))

    '''
        Построение графиков
    '''
    graph_load_cpu = report.create_graph(x=data_time[1:], 
                                         y=data_cpu[1:],
                                         filename='sng_cpu', 
                                         title_graph='Load CPU', 
                                         y_label="CPU %", 
                                         x_rlim=TIME_EXEC * 60 - 1)
    
    graph_load_memory = report.create_graph(x=data_time[1:], 
                                            y=data_memory[1:],
                                            filename='sng_memory', 
                                            title_graph='Load memory', 
                                            y_label="Memory %", 
                                            x_rlim=TIME_EXEC * 60 - 1)

    graph_load_syslog_memory = report.create_graph(x=data_time[1:], 
                                                   y=data_syslog_memory[1:],
                                                   filename='sng_syslog_memory', 
                                                   title_graph='Load syslog-ng memory', 
                                                   y_label="Memory %", 
                                                   x_rlim=TIME_EXEC * 60 - 1)

    graph_load_disk = report.create_graph(x=data_time[1:], 
                                          y=data_disk[1:],
                                          filename='sng_disk', 
                                          title_graph='Load disk', 
                                          y_label="Disk %", 
                                          x_rlim=TIME_EXEC * 60 - 1)

    report.data_to_dataframe_csv({'time': data_time, 
                                  'load_cpu': data_cpu, 
                                  'load_memory': data_memory, 
                                  'load_syslog_ng_memory': data_syslog_memory, 
                                  'load_disk': data_disk},
                                  filename='sng_data')

    '''
        Построение HTML отчета
    '''
    report.create_html([graph_load_cpu, graph_load_memory, graph_load_syslog_memory, graph_load_disk], total_rating, SERVICE_COUNT, TIME_EXEC)

    '''
        Сбор и фильтрация логов по времени (после запуска тестового сценария)
    '''
    print("\nСбор логов...\n")
    collecting_logs(os.path.expanduser(REPORT_PATH), TIME_START_SCRIPT)

    '''
        Запись информации о тестовом стенде
    '''
    info_file = open(INFO_FILENAME, 'w')
    info_file.close()

    put_system_info_in_file(TIME_START_SCRIPT.timestamp(), INFO_FILENAME)

    '''
        Архивация результатов
    '''
    print("Создание архива с отчетом...")
    libtable.Report.create_tar(os.path.expanduser(REPORT_PATH))
    print("Готово.")
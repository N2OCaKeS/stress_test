#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re
import sys
import time
import shutil
import os.path
import argparse
import subprocess
import libs.libtable as libtable
import libs.libscanner as libscanner

from os import chmod, mkdir
from libs.libsng import astra_version

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

parser.add_argument('-sc', '--service_count',
                    action='store',
                    required=False,
                    type=int,
                    default=100,
                    help='count test services',
                    dest='SERVICE_COUNT')

parser.add_argument('-t', '--time_execution',
                    required=False,
                    default=15,
                    type=int,
                    help='Load execution time in minutes',
                    dest='TIME_EXEC')

parser.add_argument('-rp', '--report_path',
                    required=True,
                    help='Absolute report path',
                    dest='REPORT_PATH')

args = parser.parse_args()


def check_exist_report_path(path):
    itog_path = os.path.expanduser(path)
    if os.path.exists(itog_path) is False:
        try:
            mkdir(itog_path)
        except PermissionError:
            print("\033[31mНедостаточно прав на создание директории {rep_path} для отчета!\033[0m".format(path))
            sys.exit(1)

def cmd(command):
    subprocess.run(command,
                   shell=True,
                   stderr=subprocess.DEVNULL)

if __name__ == '__main__':
    check_exist_report_path(args.REPORT_PATH)
    default_filter = r'(filter\sf_(dbg|debug|info|notice|warn|err(or)?|crit)\s\{\slevel\().+(\).*)'
    new_filter = r'\1{ll}\4'.format(ll=args.LOG_LEVEL)

    # Set the required logging level
    with open('/etc/syslog-ng/syslog-ng.conf', 'r') as main_syslog_ng_conf:
        new_config = re.sub(default_filter, new_filter, main_syslog_ng_conf.read())
    with open('/etc/syslog-ng/syslog-ng.conf', 'w') as main_syslog_ng_conf:
        main_syslog_ng_conf.write(new_config)

    for service_num in range(1, args.SERVICE_COUNT+1):

        # create the script and unit files
        service_name = 'dirtylogger{}.service'.format(service_num)
        shutil.copy('sng_service_template.py', '/tmp/dirtylogger{}.py'.format(service_num))
        chmod('/tmp/dirtylogger{}.py'.format(service_num), 0o0777)

        unit = ['[Unit]\n',
                'Description=test service by rkuznetsov@astralinux.ru\n',
                'After=multi-user.target\n',
                '[Service]\n',
                'Type=simple\n',
                'Restart=always\n',
                'WorkingDirectory=/media/sf_git/stress_test/syslog_ng_benchmark/\n',
                'OOMScoreAdjust = -100\n', # Prohibition on the use of the out-of-memory service and the OOM trigger mechanism
                'ExecStart=/media/sf_git/stress_test/syslog_ng_benchmark/venv/bin/python3 /tmp/dirtylogger{}.py\n'.format(service_num),
                'TimeoutSec=1\n',
                '[Install]\n',
                'WantedBy = multi - user.target\n']

        with open('/etc/systemd/system/{}'.format(service_name), 'w') as test_unit:
            test_unit.writelines(unit)

        cmd("systemctl start {}".format(service_name))

    data_cpu, data_memory, data_disk, data_time = [], [], [], []
    time_exec = args.TIME_EXEC * 60
    sc = libscanner.Scanner()

    '''
       Сбор данных с CPU, Memory, Disk 
    '''
    while time_exec > 0:
        time.sleep(0.25)
        data_cpu.append(sc.get_cpu_load())
        data_memory.append(sc.get_memory_load())
        data_disk.append(sc.get_disk_load())
        data_time.append(time_exec)
        time_exec -= 1

    '''
        Остановка сервисов
    '''
    for service_num in range(1, args.SERVICE_COUNT+1):
         cmd("systemctl stop dirtylogger{}.service".format(service_num))
    
    data_time.reverse()

    '''
        Создание отчета
    '''
    report = libtable.Report(os.path.expanduser(args.REPORT_PATH))
    rating_cpu = report.get_rating(x=data_time, y=data_cpu)
    rating_memory = report.get_rating(x=data_time, y=data_memory)
    rating_disk = report.get_rating(x=data_time, y=data_disk)
    total_rating = report.get_total_rating([rating_cpu, rating_memory, rating_disk])

    print("Rating CPU:", rating_cpu)
    print("Rating Memory:", rating_memory)
    print("Rating Disk:", rating_disk)
    print("Total rating:", total_rating)

    with open('{}/report.txt'.format(os.path.expanduser(args.REPORT_PATH)), 'w') as report_txt:
        report_txt.writelines('Astra version: {}-{}\n'.format(astra_version()[2], astra_version()[3]))
        report_txt.writelines('Service count: {}\n'.format(args.SERVICE_COUNT))
        report_txt.writelines('Load time execution: {} minutes\n'.format(args.TIME_EXEC))
        report_txt.writelines('Rating CPU: {}\n'.format(rating_cpu))
        report_txt.writelines('Rating memory: {}\n'.format(rating_memory))
        report_txt.writelines('Rating disk: {}\n'.format(rating_disk))
        report_txt.writelines('Total rating: {}\n'.format(total_rating))

    graph_load_cpu = report.create_graph(x=data_time, y=data_cpu, title_graph='Load CPU',y_label="CPU %")
    graph_load_memory = report.create_graph(x=data_time, y=data_memory, title_graph='Load memory',y_label="Memory %")
    graph_load_disk = report.create_graph(x=data_time, y=data_disk, title_graph='Load disk',y_label="Disk %")

    report.create_html([graph_load_cpu, graph_load_memory, graph_load_disk], total_rating, args.SERVICE_COUNT, args.TIME_EXEC)
    libtable.Report.create_tar(os.path.expanduser(args.REPORT_PATH))
import numpy as np
from libs.libpsb import astra_version
from time import time, strftime, gmtime
from libs.libtable import Report
from libs.libpsb import cmd
from psb_conf import INFO_FILENAME
import os
import subprocess

start_time = time()

# Пересчет рейтингов
report_dir = '/home/rkuznetsov/stress_testing_result/postgresql'
for v in ('1.7.1', '1.7.2', '1.7.3', '1.7.3.UU.1'):
    if os.path.exists('{}/{}'.format(report_dir, v)):
        for k in ('5.10', '5.15', '5.15ll'):
            if os.path.exists('{}/{}/{}/'.format(report_dir, v,k)):
                for m in ('o', 'v', 's'):
                    if os.path.exists('{}/{}/{}/{}/psb_report.txt'.format(report_dir, v, k, m)):

                        # Создать репорт
                        report = Report(param_name='clients',
                                        report_file='{}/{}/{}/{}/psb_report.txt'.format(report_dir, v, k, m))

                        report.create_beauty_table(path='{}/{}/{}/{}'.format(report_dir, v, k, m))
                        report.create_psb_cl_la_graph(report_dir='{}/{}/{}/{}'.format(report_dir, v, k, m))
                        report.create_psb_cl_tps1_graph(report_dir='{}/{}/{}/{}'.format(report_dir, v, k, m))
                        report.create_psb_cl_tps2_graph(report_dir='{}/{}/{}/{}'.format(report_dir, v, k, m))
                        report.create_psb_cl_tpsall_graph(report_dir='{}/{}/{}/{}'.format(report_dir, v, k, m))

                        print('{} {} {} {}'.format(v, k, m, report.get_total_rating()))


                        # Создать и выложить отчет
                        # полное название режима
                        fullms = {'o': 'orel',
                                  'v': 'voronezh',
                                  's': 'smolensk'}
                        fullm = fullms[m]

                        # полное название ядра
                        fullks = {
                            '1.7.2': {
                                '5.10': '5.10.0-1057-generic',
                                '5.15': '5.15.0-33-generic',
                            },
                            '1.7.3': {
                                '5.10': '5.10.142-1-generic',
                                '5.15': '5.15.0-33-generic',
                                '5.15ll': '5.15.0-33-lowlatency',
                            },
                            '1.7.3.UU.1': {
                                '5.10': '5.10.142-1-generic',
                                '5.15': '5.15.0-33-generic',
                                '5.15ll': '5.15.0-33-lowlatency',
                            },
                        }
                        fullk = fullks[v]

                        publish_cmd_lst = [
                            '/home/rkuznetsov/git/stress_test/postgresql_benchmark/venv/bin/python psb_publish.py',
                            ' --username rkuznetsov',
                            ' --token MjY3NzUwMzczNzE2OjR9Xi2vp513jB3y8+dp1imy/IIc',
                            ' --confluence-space "~rkuznetsov"',
                            ' --confluence-parent-page "{av} ⬝ PostgreSQL"'.format(av=v),
                            ' --confluence-new-page PostgreSQL_{av}_{mode}_{kernel}_low_129'.format(av=v,
                                                                                                    mode=fullm,
                                                                                                    kernel=fullk),
                            ' --package postgresql-11',
                            ' --info-path {rd}/{av}/{kernel}/{mode}/psb_info.txt'.format(rd=report_dir,
                                                                                         av=v,
                                                                                         mode=m,
                                                                                         kernel=k),
                            ' --template-path /home/rkuznetsov/git/stress_test/postgresql_benchmark/templates',
                            ' --report-path {rd}/{av}/{kernel}/{mode}'.format(rd=report_dir,
                                                                               av=v,
                                                                               mode=m,
                                                                               kernel=k)]

                        publish_cmd = ''.join(publish_cmd_lst)
                        print(publish_cmd)
                        if subprocess.run(publish_cmd,
                                          shell=True).returncode == 0:
                            print('report is published')


# lst = [1489.92, 1621.254, 1674.784, 1646.401, 1584.776, 1654.532, 1452.629, 1592.209, 1652.705, 1647.574, 1658.804, 1650.033]
# print(min(lst))
# print(max(lst))
# print(np.mean(lst))
# print(np.mean(lst) / min(lst))
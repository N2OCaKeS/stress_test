from time import time
from libs.libtable import Report
from aub_conf import \
    PSAUD_PROC_BODYS, LATENCY_REPORT_PSAUD, LOSSES_REPORT_PSAUD, \
    USERAUD_PROC_BODYS, LATENCY_REPORT_USAUD, LOSSES_REPORT_USAUD, \
    FILEAUD_PROC_BODYS, LATENCY_REPORT_FLAUD, LOSSES_REPORT_FLAUD

import os
import subprocess

start_time = time()

# Пересчет рейтингов
report_dir = '/home/rkuznetsov/stress_testing_result/auditd'
for v in ('1.7.3', '1.7.3.UU.1'):
    if os.path.exists('{}/{}'.format(report_dir, v)):

        # пакета
        pack_vs = {
            '1.7.2': '1:2.8.5-2ubuntu6+ci202206080012+astra1',
            '1.7.3': '1:2.8.5-2ubuntu6+ci202206080012+astra1+b1',
            '1.7.3.UU.1': 'auditd_1:2.8.5-2ubuntu6+ci202206080012+astra1+b1',
        }
        pack_v = pack_vs[v]

        for k in ('5.10', '5.15', '5.15ll'):
            if os.path.exists('{}/{}/{}/'.format(report_dir, v, k)):

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
                fullk = fullks[v][k]

                for m in ('o', 'v', 's'):
                    if os.path.exists('{}/{}/{}/{}/'.format(report_dir, v, k, m)):

                        # полное название режима
                        fullms = {'o': 'orel',
                                  'v': 'voronezh',
                                  's': 'smolensk'}
                        fullm = fullms[m]

                        info_lst = ['{digit_v}({mode})\n'.format(digit_v=v, mode=fullm),
                                    '{}\n'.format(fullk),
                                    '{}\n'.format(pack_v),
                                    '-\n']

                        with open('{rd}/{av}/{kernel}/{mode}/aub_info.txt'.format(rd=report_dir,
                                                                                  av=v,
                                                                                  mode=m,
                                                                                  kernel=k), 'w') as info:
                            info.writelines(info_lst)

                        for t in ('psaud', 'useraud', 'fileaud'):
                            if os.path.exists('{}/{}/{}/{}/{}'.format(report_dir, v, k, m, t)):
                                for g in ('low', 'middle', 'high'):
                                    if os.path.exists('{}/{}/{}/{}/{}/{}'.format(report_dir, v, k, m, t, g)):
                                        full_path = '{}/{}/{}/{}/{}/{}'.format(report_dir, v, k, m, t, g)
                                        if t == 'psaud':
                                            if os.path.exists(full_path + '/aub_ps_report_latency.txt')\
                                                    and os.path.exists(full_path + '/aub_ps_report_losses.txt'):

                                                # Создать репорт
                                                r = Report(PSAUD_PROC_BODYS.keys(),
                                                           full_path + '/aub_ps_report_latency.txt',
                                                           full_path + '/aub_ps_report_losses.txt')
                                                r.create_beauty_table(type='ps', path=full_path)
                                                r.create_total_latency_eps_graph(type='ps', path=full_path)
                                                r.create_total_losses_eps_graph(type='ps', path=full_path)
                                                rating = r.get_total_auditd_rating(path=full_path + '/aub_report.txt')
                                        elif t == 'useraud':
                                            if os.path.exists(full_path + '/aub_us_report_latency.txt')\
                                                    and os.path.exists(full_path + '/aub_us_report_losses.txt'):

                                                # Создать репорт
                                                r = Report(USERAUD_PROC_BODYS.keys(),
                                                           full_path + '/aub_us_report_latency.txt',
                                                           full_path + '/aub_us_report_losses.txt')
                                                r.create_beauty_table(type='us', path=full_path)
                                                r.create_total_latency_eps_graph(type='us', path=full_path)
                                                r.create_total_losses_eps_graph(type='us', path=full_path)
                                                rating = r.get_total_auditd_rating(path=full_path + '/aub_report.txt')
                                        elif t == 'fileaud':
                                            if os.path.exists(full_path + '/aub_fl_report_latency.txt')\
                                                    and os.path.exists(full_path + '/aub_fl_report_losses.txt'):

                                                # Создать репорт
                                                r = Report(FILEAUD_PROC_BODYS.keys(),
                                                           full_path + '/aub_fl_report_latency.txt',
                                                           full_path + '/aub_fl_report_losses.txt')
                                                r.create_beauty_table(type='fl', path=full_path)
                                                r.create_total_latency_eps_graph(type='fl', path=full_path)
                                                r.create_total_losses_eps_graph(type='fl', path=full_path)
                                                rating = r.get_total_auditd_rating(path=full_path + '/aub_report.txt')
                                        print('{} {} {} {} {} {}'.format(v, k, m, t, g, rating))

                                        # # Создать и выложить отчет
                                        #
                                        # # инв. данные
                                        # arms = {
                                        #     'low': ('141', 'Intel(R) Core(TM) i7-11700 CPU @ 2.50GHz', '32GB', '-'),
                                        #     'middle': ('151', 'Intel(R) Xeon(R)  CPU E5-2620 v3 @ 2.40GHz ', '31GB', '-'),
                                        #     'high': ('150', 'Intel(R) Xeon(R) Silver 4110 CPU @ 2.10GHz', '125GB', '-'),
                                        # }
                                        #
                                        # publish_cmd_lst = [
                                        #     '/home/rkuznetsov/git/stress_test/auditd_benchmark/venv/bin/python aub_publish.py',
                                        #     ' --username rkuznetsov',
                                        #     ' --token MjY3NzUwMzczNzE2OjR9Xi2vp513jB3y8+dp1imy/IIc',
                                        #     ' --confluence-space "~rkuznetsov"',
                                        #     ' --confluence-parent-page "{av} ⬝ Auditd"'.format(av=v),
                                        #     ' --confluence-new-page {test}_{av}_{mode}_{kernel}_{greid}_{num}'.format(test=t,
                                        #                                                                               av=v,
                                        #                                                                               mode=fullm,
                                        #                                                                               greid=g,
                                        #                                                                               num=arms[g][0],
                                        #                                                                               kernel=fullk),
                                        #     ' --package auditd',
                                        #     ' --test-set {}'.format(t),
                                        #     ' --info-path {rd}/{av}/{kernel}/{mode}/aub_info.txt'.format(rd=report_dir,
                                        #                                                                  av=v,
                                        #                                                                  mode=m,
                                        #                                                                  kernel=k),
                                        #     ' --template-path /home/rkuznetsov/git/stress_test/auditd_benchmark/templates',
                                        #     ' --report-path {rd}/{av}/{kernel}/{mode}/{test}/{greid}'.format(rd=report_dir,
                                        #                                                                      av=v,
                                        #                                                                      mode=m,
                                        #                                                                      test=t,
                                        #                                                                      greid=g,
                                        #                                                                      kernel=k),
                                        #     ' --arm-proccessor "{}"'.format(arms[g][1]),
                                        #     ' --arm-memory "{}"'.format(arms[g][2]),
                                        #     ' --arm-storage "{}"'.format(arms[g][3]),
                                        #     ]
                                        #
                                        # publish_cmd = ''.join(publish_cmd_lst)
                                        # print(publish_cmd)
                                        # if subprocess.run(publish_cmd,
                                        #                   shell=True).returncode == 0:
                                        #     print('report is published')
                                    else:
                                        pass
from libs.libtable import Report
from fsb_conf import FILES, FILES_STEP, FILES_LIMIT
from time import time

import subprocess
import os

# Пересчет рейтингов
start_time = time()
dir = '/home/rkuznetsov/stress_testing_result/fs'
for v in ('1.7.1', '1.7.2', '1.7.3', '1.7.3.UU.1'):
    if os.path.exists('{}/{}'.format(dir, v)):
        for k in ('5.10', '5.15', '5.15ll'):
            if os.path.exists('{}/{}/{}/'.format(dir, v, k)):
                for m in ('o', 'v', 's'):
                    if os.path.exists('{}/{}/{}/{}/'.format(dir, v, k, m)):
                        for fs in ('ext4', 'xfs', 'ntfs'):
                            if os.path.exists('{}/{}/{}/{}/{}'.format(dir, v, k, m, fs)):

                                report_dir = '{}/{}/{}/{}/{}'.format(dir, v, k, m, fs)
                                report_file = '{}/{}/{}/{}/{}/fsb_report.txt'.format(dir, v, k, m, fs)

                                report = Report(ox_lo_lim=FILES,
                                                ox_step=FILES_STEP,
                                                ox_up_lim=FILES_LIMIT,
                                                report=report_dir)

                                report.create_beauty_table(path=report_dir)
                                report.create_fsb_fc_sp_graph(path=report_dir)
                                report.create_fsb_fc_app_overhead_graph(path=report_dir)
                                report.create_fsb_fc_create_graph(path=report_dir)
                                report.create_fsb_fc_write_graph(path=report_dir)
                                report.create_fsb_fc_fsync_graph(path=report_dir)
                                report.create_fsb_fc_sync_graph(path=report_dir)
                                report.create_fsb_fc_close_graph(path=report_dir)
                                report.create_fsb_fc_unlink_graph(path=report_dir)
                                print('{} {} {} {} {}'.format(v, k, m, fs, report.get_total_rating(x_lst=report.file_count_lst)))

                                # Команда публикации расчетов
                                # Создать и выложить отчет
                                fullms = {'o': 'orel',
                                          'v': 'voronezh',
                                          's': 'smolensk'}
                                fullm = fullms[m]

                                # полное название ядра
                                fullks = {
                                    '1.7.1': {
                                        '5.10': '5.10.0-1045-generic',
                                    },
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

                                publish_cmd_lst = [
                                    '/home/rkuznetsov/git/stress_test/file_system_benchmark/venv/bin/python fsb_publish.py',
                                    ' --username rkuznetsov',
                                    ' --token MjY3NzUwMzczNzE2OjR9Xi2vp513jB3y8+dp1imy/IIc',
                                    ' --confluence-space "~rkuznetsov"',
                                    ' --confluence-parent-page "{av} ⬝ Файловые системы"'.format(av=v),
                                    ' --confluence-new-page {fs}_{av}_{mode}_{kernel}_low_129'.format(fs=fs.upper(),
                                                                                                      av=v,
                                                                                                      mode=fullm,
                                                                                                      kernel=fullk),
                                    ' --file-system {}'.format(fs),
                                    ' --test-set fs_mark_count',
                                    ' --info-path {}/fsb_info.txt'.format(report_dir),
                                    ' --template-path /home/rkuznetsov/git/stress_test/file_system_benchmark/templates',
                                    ' --report-path {}'.format(report_dir)]

                                publish_cmd = ''.join(publish_cmd_lst)
                                print(publish_cmd)
                                if subprocess.run(publish_cmd,
                                                  shell=True).returncode == 0:
                                    print('report is published')

# -*- coding: UTF-8 -*-

# ;===========================================================
# ; Author: rkuznetsov@astralinux.ru
# ; Date: 2022
# ;===========================================================

import argparse
import os
import subprocess
from libs.libreport import ReportToConfluence
from libs.libfsb import astra_version
from libs.libtable import Report
from fsb_conf import REPORT_PATH, TEMPLATE_PATH, \
    FILES, FILES_STEP, FILES_LIMIT, \
    SIZE, SIZE_STEP, SIZE_LIMIT

DESCRIPTION = ""
parser = argparse.ArgumentParser(description=DESCRIPTION)
parser.add_argument('-u', '--username',
                    action='store',
                    required=True,
                    help='confluence user',
                    dest='USER')

parser.add_argument('-p', '--password',
                    action='store',
                    required=True,
                    help='confluence password',
                    dest='PASSWD')

parser.add_argument('-cs', '--confluence-space',
                    action='store',
                    required=True,
                    help='confluence space',
                    dest='SPACE')

parser.add_argument('-cpp', '--confluence-parent-page',
                    action='store',
                    required=True,
                    help='confluence parent page',
                    dest='PPAGE')

parser.add_argument('-cnp', '--confluence-new-page',
                    action='store',
                    required=True,
                    help='confluence new page',
                    dest='NPAGE')

parser.add_argument('-pack', '--package',
                    action='store',
                    required=True,
                    help='test package',
                    dest='PACKAGE')

parser.add_argument('-rp', '--report-path',
                    action='store',
                    required=False,
                    default=REPORT_PATH,
                    help='path to report files',
                    dest='R_PATH')

parser.add_argument('-tp', '--tarfile-path',
                    action='store',
                    required=False,
                    default=None,
                    help='path to tar with report',
                    dest='TAR_PATH')

parser.add_argument('-ts', '--test-set',
                    action='store',
                    choices=['fs_mark_count',
                             'fs_mark_size'],
                    required=True,
                    dest='TS')

parser.add_argument('-an', '--arm-number',
                    action='store',
                    required=False,
                    default='129',
                    help='stand number',
                    dest='ARM_NUM')

parser.add_argument('-ap', '--arm-proccessor',
                    action='store',
                    required=False,
                    default='Intel(R) Core(TM) i5-8600K CPU @ 3.60GHz',
                    help='processor on stand',
                    dest='ARM_PROC')

parser.add_argument('-am', '--arm-memory',
                    action='store',
                    required=False,
                    default='32GB',
                    help='RAM on stand',
                    dest='ARM_MEM')

parser.add_argument('-as', '--arm-storage',
                    action='store',
                    required=False,
                    default='SSD 512GB',
                    help='system storage on stand',
                    dest='ARM_ST')

args = parser.parse_args()

confluence_report = ReportToConfluence(username=args.USER, password=args.PASSWD)

# если получен архив, распаковать
if args.TAR_PATH is not None:
    confluence_report.unzip_tarfile(args.TAR_PATH)

# создать страницу confluence
confluence_report.create_confluence_page(args.SPACE,
                                         args.PPAGE,
                                         args.NPAGE)
# прикрепить файлы к странице confluence
for file in os.listdir(args.R_PATH):
    confluence_report.attache_files('{}/{}'.format(args.R_PATH, file),
                                    args.SPACE,
                                    args.NPAGE)

# генерация вступительной таблицы
with open('{}/header_table_template.html'.format(TEMPLATE_PATH), 'r') as file:
    header_table_temp = file.read()
    header_table = header_table_temp.format(av='{digit_v}({mode})'.format(digit_v=astra_version()[0],
                                                                          mode=astra_version()[1]),
                                            kernel=subprocess.run('uname -r',
                                                                  shell=True,
                                                                  stdout=subprocess.PIPE).stdout.decode("utf-8"),
                                            package_name=args.PACKAGE,
                                            package_vers=subprocess.run("dpkg -l "+args.PACKAGE+" | awk '{print $3}' | tail -n1",
                                                                        shell=True,
                                                                        stdout=subprocess.PIPE).stdout.decode("utf-8"),
                                            param_files='{}-{}/{}'.format(FILES,
                                                                          FILES_LIMIT,
                                                                          FILES_STEP),
                                            param_size='{}-{}/{}'.format(SIZE,
                                                                         SIZE_LIMIT,
                                                                         SIZE_STEP),
                                            arm_num=args.ARM_NUM,
                                            arm_proc=args.ARM_PROC,
                                            arm_mem=args.ARM_MEM,
                                            arm_st=args.ARM_ST)


# создание страницы отчета
with open('{}/rating_template.html'.format(TEMPLATE_PATH), 'r') as template:
    rating_temp = template.read()
    if args.TS == 'fs_mark_count':
        rep = Report(ox_lo_lim=FILES, ox_up_lim=FILES_LIMIT)
        rating = rating_temp.format(r=str(rep.get_total_rating(rep.file_count_lst)))
    if args.TS == 'fs_mark_size':
        rep = Report(ox_lo_lim=SIZE, ox_up_lim=SIZE_LIMIT)
        rating = rating_temp.format(r=str(rep.get_total_rating(rep.file_size_lst)))

with open('{}/fsb_report_table.html'.format(REPORT_PATH), 'r') as file:
    main_table = file.read()

with open('{}/img_template.html'.format(TEMPLATE_PATH), 'r') as template:
    images_lst = []
    img_temp = template.read()
    for file in os.listdir(args.R_PATH):
        if file.endswith('png'):
            images_lst.append(img_temp.format(page_id=confluence_report.get_confluence_page_id(args.SPACE, args.NPAGE), img_png=file))
    images = '\n'.join(images_lst)

html_page = '\n'.join([header_table, rating, main_table, images])

# выкладываем информацию на страницу
confluence_report.update_confluence_page(args.SPACE, args.NPAGE, html_page)
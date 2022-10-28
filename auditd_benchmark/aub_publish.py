# -*- coding: UTF-8 -*-

# ;===========================================================
# ; Author: rkuznetsov@astralinux.ru
# ; Date: 2022
# ;===========================================================

import argparse
import os
import subprocess
from pathlib import Path
from libs.libreport import ReportToConfluence, ReportToJira
from libs.libaub import astra_version
from libs.libtable import Report
from aub_conf import REPORT_DIR, TEMPLATE_DIR, \
    PS_LOWER_LIMIT, PS_UPPER_LIMIT, PS_STEP, DEFAULT_PS_LIFETIME, DEFAULT_PS_EVENT_RE_INITIALIZATION_DELAY

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

parser.add_argument('-ji', '--jira-issue',
                    action='store',
                    required=False,
                    help='jira issue',
                    dest='JIRA_ISSUE')

parser.add_argument('-pack', '--package',
                    action='store',
                    required=True,
                    help='test package',
                    dest='PACKAGE')

parser.add_argument('-rp', '--report-path',
                    action='store',
                    required=False,
                    default=REPORT_DIR,
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
jira_report = ReportToJira(username=args.USER, password=args.PASSWD)

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
with open('{}/header_table_template.html'.format(TEMPLATE_DIR), 'r') as file:
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
                                            param_ps_lifetime=DEFAULT_PS_LIFETIME,
                                            param_ps_delay=DEFAULT_PS_EVENT_RE_INITIALIZATION_DELAY,
                                            param_ps_quantity='{}-{}/{}'.format(PS_LOWER_LIMIT,
                                                                                PS_UPPER_LIMIT,
                                                                                PS_STEP),

                                            arm_num=args.ARM_NUM,
                                            arm_proc=args.ARM_PROC,
                                            arm_mem=args.ARM_MEM,
                                            arm_st=args.ARM_ST)


# создание страницы отчета
with open('{}/rating_template.html'.format(TEMPLATE_DIR), 'r') as template:
    rating_temp = template.read()
    if args.TS == 'psaud':
        rep = Report()
        rating = rating_temp.format(total_latency_rating=str(rep.get_total_latency_rating()),
                                    total_losses_rating=str(rep.get_total_losses_rating()),
                                    total_auditd_rating=str(rep.get_total_auditd_rating()))

tables = ''
for file in Path(REPORT_DIR).glob('aub_*_table.html'):
    with open(file, 'r') as f:
        tables + f.read() + '\n'

# подготовка изображений
with open('{}/img_template.html'.format(TEMPLATE_DIR), 'r') as template:
    images_lst = []
    img_temp = template.read()
    for file in os.listdir(args.R_PATH):
        if file.endswith('png'):
            images_lst.append(img_temp.format(page_id=confluence_report.get_confluence_page_id(args.SPACE, args.NPAGE), img_png=file))
    images = '\n'.join(images_lst)

html_page = '\n'.join([header_table, rating, tables, images])

# выкладываем информацию на страницу
confluence_report.update_confluence_page(args.SPACE, args.NPAGE, html_page)

if args.JIRA_ISSUE:
    with open('{}/issue_comment_template.txt'.format(TEMPLATE_DIR), 'r') as template:
        comment = template.read()
        jira_report.add_comment_to_issue(args.JIRA_ISSUE,
                                         comment.format(url=confluence_report.get_confluence_public_url(args.SPACE,
                                                                                                        args.NPAGE)))
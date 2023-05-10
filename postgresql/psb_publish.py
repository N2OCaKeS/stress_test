# -*- coding: UTF-8 -*-

# ;===========================================================
# ; Author: rkuznetsov@astralinux.ru
# ; Date: 2022
# ;===========================================================

import argparse
import os
import subprocess
from libs.libreport import ReportToConfluence, ReportToJira
from libs.libpsb import astra_version, perf, dump
from libs.libtable import Report
from psb_conf import DEFAULT_SCALE_FACTOR, DEFAULT_TRANSACTIONS, DEFAULT_THREADS, \
    CLIENTS, CLIENTS_STEP, LIMITE_CLIENTS, REPORT_PATH, TEMPLATE_PATH, INFO_FILENAME, GRAPH_DESCRIPTIONS

DESCRIPTION = ""
parser = argparse.ArgumentParser(description=DESCRIPTION)
parser.add_argument('-u', '--username',
                    action='store',
                    required=True,
                    help='confluence user',
                    dest='USER')

parser.add_argument('-p', '--password',
                    action='store',
                    required=False,
                    default=None,
                    help='confluence password',
                    dest='PASSWD')

parser.add_argument('-t', '--token',
                    action='store',
                    required=False,
                    default=None,
                    help='confluence access token',
                    dest='TOKEN')

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
                    default=REPORT_PATH,
                    help='path to dir with report files',
                    dest='R_PATH')

parser.add_argument('-tp', '--template-path',
                    action='store',
                    required=False,
                    default=TEMPLATE_PATH,
                    help='path to dir with template files',
                    dest='T_PATH')

parser.add_argument('-ip', '--info-path',
                    action='store',
                    required=False,
                    default=INFO_FILENAME,
                    help='path to info file',
                    dest='I_PATH')

parser.add_argument('-tarp', '--tarfile-path',
                    action='store',
                    required=False,
                    default=None,
                    help='path to tar with report',
                    dest='TAR_PATH')

parser.add_argument('-an', '--arm-name',
                    action='store',
                    required=False,
                    default='low(141)',
                    help='stand_level(stand_number)',
                    dest='ARM_NAME')

parser.add_argument('-ap', '--arm-proccessor',
                    action='store',
                    required=False,
                    default='Intel(R) Core(TM) i7-11700 CPU @ 2.50GHz',
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
                    default='Samsung NVME 970 EVO 2Тб',
                    help='system storage on stand',
                    dest='ARM_ST')

args = parser.parse_args()

confluence_report = ReportToConfluence(username=args.USER, password=args.PASSWD, token=args.TOKEN)
jira_report = ReportToJira(username=args.USER, password=args.PASSWD, token=args.TOKEN)

#создать flamegraph
print('# INFO # --- flamegraph')
perf()

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
with open(args.I_PATH) as info:
    info_lst = info.read().split('\n')
with open('{}/header_table_template.html'.format(args.T_PATH), 'r') as file:
    header_table_temp = file.read()
    header_table = header_table_temp.format(av=info_lst[0],
                                            kernel=info_lst[1],
                                            package_name=args.PACKAGE,
                                            package_vers=info_lst[2],
                                            param_scale=str(DEFAULT_SCALE_FACTOR),
                                            param_tr=str(DEFAULT_TRANSACTIONS),
                                            param_th=str(DEFAULT_THREADS),
                                            param_cl='{}-{}/{}'.format(CLIENTS, LIMITE_CLIENTS, CLIENTS_STEP),
                                            arm_num=args.ARM_NAME,
                                            arm_proc=args.ARM_PROC,
                                            arm_mem=args.ARM_MEM,
                                            arm_st=args.ARM_ST,
                                            lead_time=info_lst[3])


# создание страницы отчета
rep = Report(report_file='{}/psb_report.txt'.format(args.R_PATH))
with open('{}/rating_template.html'.format(args.T_PATH), 'r') as template:
    rating_temp = template.read()
    rating = rating_temp.format(r=str(rep.get_total_rating()))

with open('{}/psb_report_table.html'.format(args.R_PATH), 'r') as file:
    main_table = file.read()

with open('{}/img_template.html'.format(args.T_PATH), 'r') as template:
    images_lst = []
    img_temp = template.read()
    for file in os.listdir(args.R_PATH):
        if file.endswith('png'):
            images_lst.append(img_temp.format(page_id=confluence_report.get_confluence_page_id(args.SPACE, args.NPAGE),
                                              img_png=file,
                                              description=GRAPH_DESCRIPTIONS[file]))
    images = '\n'.join(images_lst)

html_page = '\n'.join([header_table, rating, main_table, images])

# выкладываем информацию на страницу
confluence_report.update_confluence_page(args.SPACE, args.NPAGE, html_page)

if args.JIRA_ISSUE:
    with open('{}/issue_comment_template.txt'.format(args.T_PATH), 'r') as template:
        comment = template.read()
        jira_report.add_comment_to_issue(args.JIRA_ISSUE,
                                         comment.format(url=confluence_report.get_confluence_public_url(args.SPACE,
                                                                                                        args.NPAGE)))
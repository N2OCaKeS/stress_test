# -*- coding: UTF-8 -*-

# ;===========================================================
# ; Author: rkuznetsov@astralinux.ru
# ; Date: 2023
# ;===========================================================

import argparse

from os import listdir
from re import search
from pathlib import Path
from libs.libreport import ReportToConfluence, ReportToJira
from lsb_conf import REPORT_DIR, TEMPLATE_DIR, INFO_FILENAME, GRAPH_DESCRIPTIONS, \
    STAND1_LOWER_LIMIT, STAND1_UPPER_LIMIT, STAND1_STEP, \
    STAND2_LOWER_LIMIT, STAND2_UPPER_LIMIT, STAND2_STEP, \
    STAND3_LOWER_LIMIT, STAND3_UPPER_LIMIT, STAND3_STEP, \
    STAND4_LOWER_LIMIT, STAND4_UPPER_LIMIT, STAND4_STEP

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

parser.add_argument('-rp', '--report-path',
                    action='store',
                    required=False,
                    default=REPORT_DIR,
                    help='path to report files',
                    dest='R_PATH')

parser.add_argument('-tp', '--template-path',
                    action='store',
                    required=False,
                    default=TEMPLATE_DIR,
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
                    default='low(129)',
                    help='stand_level(stand_number)',
                    dest='ARM_NAME')

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

if args.ARM_NAME == 'low(141)':
    lower_limit = STAND1_LOWER_LIMIT
    upper_limit = STAND1_UPPER_LIMIT
    step = STAND1_STEP
elif args.ARM_NAME == 'low(129)':
    lower_limit = STAND2_LOWER_LIMIT
    upper_limit = STAND2_UPPER_LIMIT
    step = STAND2_STEP
elif args.ARM_NAME == 'middle(151)':
    lower_limit = STAND3_LOWER_LIMIT
    upper_limit = STAND3_UPPER_LIMIT
    step = STAND3_STEP
elif args.ARM_NAME == 'high(150)':
    lower_limit = STAND4_LOWER_LIMIT
    upper_limit = STAND4_UPPER_LIMIT
    step = STAND4_STEP
else:
    exit(2)

confluence_report = ReportToConfluence(username=args.USER, password=args.PASSWD, token=args.TOKEN)
jira_report = ReportToJira(username=args.USER, password=args.PASSWD, token=args.TOKEN)

# если получен архив, распаковать
if args.TAR_PATH is not None:
    confluence_report.unzip_tarfile(args.TAR_PATH)

# создать страницу confluence
confluence_report.create_confluence_page(args.SPACE,
                                         args.PPAGE,
                                         args.NPAGE)
# прикрепить файлы к странице confluence
for file in listdir(args.R_PATH):
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
                                            param_threads_quantity='{}-{}/{}'.format(lower_limit,
                                                                                     upper_limit,
                                                                                     step),

                                            arm_num=args.ARM_NAME,
                                            arm_proc=args.ARM_PROC,
                                            arm_mem=args.ARM_MEM,
                                            arm_st=args.ARM_ST,
                                            lead_time=info_lst[3])


# создание страницы отчета
with open('{}/rating_template.html'.format(args.T_PATH), 'r') as template:
    rating_temp = template.read()
with open('{}/aub_report.txt'.format(args.R_PATH), 'r') as report:
    report_temp = report.read()
    rating = rating_temp.format(type=args.TS,
                                dhry2reg_rating=search(r'dhry2reg: (-?\d+.\d+)', report_temp).group(1),
                                whetstone_double_rating=search(r'whetstone-double: (-?\d+.\d+)', report_temp).group(1),
                                execl_rating=search(r'execl: (-?\d+.\d+)', report_temp).group(1),
                                fstime_rating=search(r'fstime: (-?\d+.\d+)', report_temp).group(1),
                                fsbuffer_rating=search(r'fsbuffer: (-?\d+.\d+)', report_temp).group(1),
                                fsdisk_rating=search(r'fsdisk: (-?\d+.\d+)', report_temp).group(1),
                                pipe_rating=search(r'pipe: (-?\d+.\d+)', report_temp).group(1),
                                context1_rating=search(r'context1: (-?\d+.\d+)', report_temp).group(1),
                                spawn_rating=search(r'spawn: (-?\d+.\d+)', report_temp).group(1),
                                shell1_rating=search(r'shell1: (-?\d+.\d+)', report_temp).group(1),
                                shell8_rating=search(r'shell8: (-?\d+.\d+)', report_temp).group(1),
                                syscall_rating=search(r'syscall: (-?\d+.\d+)', report_temp).group(1))

tables = ''
for file in Path(args.R_PATH).glob('lsb_*_table.html'):
    with open(file, 'r') as f:
        tables + f.read() + '\n'

# подготовка изображений
with open('{}/img_template.html'.format(args.T_PATH), 'r') as template:
    images_lst = []
    img_temp = template.read()
    for file in listdir(args.R_PATH):  # идем по списку
        if file.endswith('png'):  # если png
            images_lst.append(img_temp.format(page_id=confluence_report.get_confluence_page_id(args.SPACE, args.NPAGE),
                                              img_png=file,
                                              description=GRAPH_DESCRIPTIONS[file]))  # добавляем в list
    images = '\n'.join(images_lst)

html_page = '\n'.join([header_table, rating, tables, images])

# выкладываем информацию на страницу
confluence_report.update_confluence_page(args.SPACE, args.NPAGE, html_page)

if args.JIRA_ISSUE:
    with open('{}/issue_comment_template.txt'.format(args.T_PATH), 'r') as template:
        comment = template.read()
        jira_report.add_comment_to_issue(args.JIRA_ISSUE,
                                         comment.format(url=confluence_report.get_confluence_public_url(args.SPACE,
                                                                                                        args.NPAGE)))
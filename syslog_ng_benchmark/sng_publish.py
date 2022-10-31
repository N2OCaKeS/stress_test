# -*- coding: UTF-8 -*-

import argparse
import os
import sys
import subprocess
from libs.libreport import ReportToConfluence, ReportToJira
from libs.libsng import astra_version


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
                    required=True,
                    help='path to report files',
                    dest='R_PATH')

parser.add_argument('-tp', '--tarfile-path',
                    action='store',
                    required=False,
                    default=None,
                    help='path to tar with report',
                    dest='TAR_PATH')

parser.add_argument('-an', '--arm-number',
                    action='store',
                    required=False,
                    default='141',
                    help='stand number',
                    dest='ARM_NUM')

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

confluence_report = ReportToConfluence(username=args.USER, password=args.PASSWD)
jira_report = ReportToJira(username=args.USER, password=args.PASSWD)

# если получен архив, распаковать
if args.TAR_PATH is not None:
    confluence_report.unzip_tarfile(args.TAR_PATH)

with open('{}/{}'.format(args.R_PATH, 'sng_report.txt')) as report_txt:
    for line in report_txt.readline():
        if line in "Load_time_execution":
            TIME_EXEC = line.split(" ")[1]
        if line in "Service_count":
            SERVICE_COUNT = line.split(" ")[1]
        if line in "Total_rating":
            TOTAL_RATING = line.split(" ")[1]


if "templates" in os.listdir(os.getcwd()):
    TEMPLATE_PATH = os.getcwd() + 'templates/'
else:
    print("Перейдите в директорию syslog_ng_benchmark!")
    sys.exit()

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
                                            param_time_exec=TIME_EXEC,
                                            param_service_count=SERVICE_COUNT,
                                            arm_num=args.ARM_NUM,
                                            arm_proc=args.ARM_PROC,
                                            arm_mem=args.ARM_MEM,
                                            arm_st=args.ARM_ST)


with open('{}/rating_template.html'.format(TEMPLATE_PATH), 'r') as template:
    rating_temp = template.read()
    rating = rating_temp.format(r=TOTAL_RATING)


with open('{}/img_template.html'.format(TEMPLATE_PATH), 'r') as template:
    images_lst = []
    img_temp = template.read()
    for file in os.listdir(args.R_PATH):
        if file.endswith('png'):
            images_lst.append(img_temp.format(page_id=confluence_report.get_confluence_page_id(args.SPACE, args.NPAGE), img_png=file))
    images = '\n'.join(images_lst)

html_page = '\n'.join([header_table, rating, images])

# выкладываем информацию на страницу
confluence_report.update_confluence_page(args.SPACE, args.NPAGE, html_page)

if args.JIRA_ISSUE:
    with open('{}/issue_comment_template.txt'.format(TEMPLATE_PATH), 'r') as template:
        comment = template.read()
        jira_report.add_comment_to_issue(args.JIRA_ISSUE,
                                         comment.format(url=confluence_report.get_confluence_public_url(args.SPACE,
                                                                                                        args.NPAGE)))

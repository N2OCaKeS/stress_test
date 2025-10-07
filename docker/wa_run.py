import subprocess
import sys
from os import path
import argparse
from docker_conf import VENV_PATH
from libs.zefir import UploaderZC
from libs.libtable import Report
from allta import SystemCommands

parser = argparse.ArgumentParser()
parser.add_argument("-wa",
                    action='store_true',
                    help="Choose test name.",
                    dest="TEST")
parser.add_argument('-u', '--username',
                    action='store',
                    required=True,
                    help='confluence user',
                    dest='USER')

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

parser.add_argument('-sn', '--stand-num',
                    action='store',
                    choices=['1',
                             '2',
                             '3',
                             '4',
                             '5',
                             '6',
                             '7',
                             '8',
                             '9',
                             '10',
                             '11',
                             '12',
                             '13'],
                    required=True,
                    help='stand num',
                    dest='STAND')

parser.add_argument('-fti', '--folder-tree-id',
                    action='store',
                    required=True,
                    help='folder-tree-id',
                    dest='FTI')

parser.add_argument('-tcyc', '--test-cycle-name',
                    action='store',
                    required=True,
                    help='test-cycle-name',
                    dest='TCYC')

parser.add_argument('-tcas', '--test-case-name',
                    action='store',
                    required=True,
                    help='test-case-name',
                    dest='TCAS')

parser.add_argument('-ba', '--basic-auth',
                    action='store',
                    required=True,
                    help='basic-auth',
                    dest='BA')

parser.add_argument('-tcv', '--test-cycle-version',
                    action='store',
                    required=True,
                    help='test-cycle-version',
                    dest='TCV')
args = parser.parse_args()


if __name__ == "__main__":

    uzs = UploaderZC(folder_tree_id=args.FTI,
                 test_cycle_name=args.TCYC,
                 test_case_name=args.TCAS,
                 basic_auth=args.BA,
                 test_cycle_version=args.TCV,
                 token=args.TOKEN,
                 username=args.USER,
                 conf_space=args.SPACE,
                 conf_parent_page=args.PPAGE,
                 conf_new_page_name=args.NPAGE,
                 grade_stand=args.STAND)

    uzs.upload_test_cycle_status('progress')
    
    """
        TODO Здесь запускаем тесты
    """
    sc = SystemCommands()
    if args.TEST:
        # Start
        if sc.cmd_with_returncode("cd ./site && bash start.sh final") == 0:
            # Total Rating / HTML
            report = Report()
            report.wa_report()

            uzs.public = True
            #uzs.statistics = True
            uzs.upload_test_cycle_status(zefir_status='pass')
        else:
            print('Тест завершился с иключением')
            uzs.upload_test_cycle_status(zefir_status='fail')
    else: "Тест не найден"

    
               
if path.isfile('libs/zefir.log'):
    with open('libs/zefir.log', 'r') as r:
        zefir_log = r.read()
        print('\n\n\nZefir-log\n')
        print(zefir_log)
if path.isfile('JIRA_ERROR.log'):
    with open('JIRA_ERROR.log', 'r') as r:
        jira_log = r.read()
        print('\n\n\nJira-log\n')
        print(jira_log)



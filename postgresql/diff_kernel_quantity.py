import argparse
from libs.libpsb import BaseTest
from libs.zefir import UploaderZC
import pandas as pd
from os import path


file_name = 'results.csv'
parser = argparse.ArgumentParser()
parser.add_argument('-q',
                    action='store',
                    required=True,
                    help='kernels quantity',
                    dest='KERNELS_QUANTITY')

parser.add_argument('-st',
                    action='store',
                    required=True,
                    help='stand number',
                    dest='STAND')

parser.add_argument('-sf',
                    action='store',
                    choices=['begin', 
                             'end'],
                    required=False,
                    help='start or finish test',
                    dest='SF')

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

parser.add_argument('-u', '--username',
                    action='store',
                    required=True,
                    help='confluence user',
                    dest='USER')

parser.add_argument('-tk', '--token',
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

parser.add_argument('-pack', '--package',
                    action='store',
                    required=True,
                    help='test package',
                    dest='PACKAGE')

parser.add_argument('-sn', '--stand-num',
                    action='store',
                    choices=['1',
                             '3',
                             '4'],
                    required=True,
                    help='stand num',
                    dest='STAND')

args = parser.parse_args()

test = BaseTest(database='psql',
                storage_device='NVME',
                stand_number=args.STAND,
                file_name=file_name)

def dates_prepare():
    data = {
            'Kernels':str(args.KERNELS_QUANTITY),
            'tps':test.test_run(clients=800, 
                                repeat=20)
            }

    return data

if args.SF == 'begin':
    uzs = UploaderZC(folder_tree_id=args.FTI,
                    test_cycle_name=args.TCYC,
                    test_case_name=args.TCAS,
                    basic_auth=args.BA,
                    test_cycle_version=args.TCV,
                    token=args.TOKEN,
                    username=args.USER)
    uzs.upload_test_cycle_status('progress')


if not path.isfile(file_name):
    results = pd.DataFrame(dates_prepare(), index=[0])
    print(results)
    results.to_csv(file_name, index=False)
else:
    results_from_csv = pd.read_csv(file_name, delimiter=',')
    print(results_from_csv)
    test.prepare = False
    results = pd.DataFrame(dates_prepare(), index=[0])
    results = pd.concat([results_from_csv, results], ignore_index=True)
    print(results)
    results.to_csv(file_name, index=False)


if args.SF == 'end':
    uzs.conf_space = args.SPACE
    uzs.conf_parent_page = args.PPAGE
    uzs.conf_new_page_name = args.NPAGE
    uzs.grade_stand = args.STAND
    uzs.package = args.PACKAGE
    uzs.public = True
    uzs.kernel_check = True
    if test.check_conditions():
        uzs.upload_test_cycle_status(zefir_status='pass')
    else:
        uzs.upload_test_cycle_status(zefir_status='fail')


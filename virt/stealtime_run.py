from virt_test import StealTime
from virt_conf import LOW, HIGH, REPORT_PATH
from libs.virtlib import info_list
from libs.zefir import UploaderZC
import argparse


parser = argparse.ArgumentParser()
parser.add_argument('-sn',
                    action='store',
                    required=True,
                    help='stand number',
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

parser.add_argument('-tcv', '--test-cycle-version',
                    action='store',
                    required=True,
                    help='test-cycle-version',
                    dest='TCV')

parser.add_argument('-vbox', 
                    action='store',
                    required=True,
                    help='vbox name',
                    dest='VBOX')

args = parser.parse_args()


uzs = UploaderZC(folder_tree_id=args.FTI,
                test_cycle_name=args.TCYC,
                test_case_name=args.TCAS,
                basic_auth=args.BA,
                test_cycle_version=args.TCV,
                token=args.TOKEN,
                username=args.USER,
                grade_stand=args.STAND,
                conf_space=args.SPACE,
                conf_parent_page=args.PPAGE,
                conf_new_page_name=args.NPAGE)


low_load_test = StealTime(rc_vbox=args.VBOX,
                          vm_count=LOW,
                          testdir=REPORT_PATH,
                          load_type='low')

high_load_test = StealTime(rc_vbox=args.VBOX,
                           vm_count=HIGH,
                           testdir=REPORT_PATH,
                           load_type='high')

uzs.upload_test_cycle_status(zefir_status='progress')

#Start test
low_load_test.prepare_and_start()
low_load_test.vms_destroy()
if low_load_test.results_processing() == LOW:
    print('Low load test successfully done')
else: uzs.upload_test_cycle_status(zefir_status='fail')

high_load_test.prepare_and_start()
high_load_test.vms_destroy()
if high_load_test.results_processing() == HIGH:
    print('High load test successfully done')
else: uzs.upload_test_cycle_status(zefir_status='fail')

info_list()
uzs.public = True
#uzs.statistics = True
uzs.upload_test_cycle_status(zefir_status='pass')



import argparse
from allta import UploaderZC
from datetime import datetime

from libs.libnet import get_duration
from libs.libtests import Sigmentation_fault
from libs.libpublic import kernel_publisher

from kernel_conf import BASE_PATH, SEGMENTATION_FAULT_VM_COUNT, SEGMENTATION_FAULT_VCPU, SEGMENTATION_FAULT_RAM


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

parser.add_argument('-testname', 
                    action='store',
                    required=True,
                    help='test name',
                    dest='TESTNAME')

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
                conf_new_page_name=args.NPAGE,
                testname=args.TESTNAME)
uzs.upload_test_cycle_status(zefir_status='progress')


#Start test
if args.TESTNAME == 'iof':
    time_start_script = datetime.now()
    
    sigmentation_fault = Sigmentation_fault(rc_name=args.TCV,
                                testdir=BASE_PATH,
                                vm_count=SEGMENTATION_FAULT_VM_COUNT,
                                vcpu=SEGMENTATION_FAULT_VCPU,
                                ram=SEGMENTATION_FAULT_RAM)

    sigmentation_fault.prepare_vms()
    sigmentation_fault.start_test()
    sigmentation_fault.vms_destroy()
    sigmentation_fault.results_processing()


    lead_time = get_duration((datetime.now() - time_start_script).total_seconds())
    publisher = kernel_publisher(
        username=args.USER,
        token=args.TOKEN,
        space=args.SPACE,
        parent_title=args.PPAGE,
        title=args.NPAGE,
        stand_number=args.STAND,
        lead_time=lead_time,
        test_cycle_version=args.TCV,
    )

    uzs.upload_test_cycle_status(zefir_status='pass')

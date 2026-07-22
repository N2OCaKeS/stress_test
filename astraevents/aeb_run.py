import argparse
from allta import UploaderZC, SystemCommands
from datetime import datetime

from libs.libaeb import get_duration
from libs.libtests import AstraEventsLoadTest
from libs.libpublic import astra_events_publisher

from aeb_conf import BASE_PATH, VM_COUNT, VCPU_MAX, RAM_MAX, VCPU_MIN, RAM_MIN


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
                username=args.USER)
uzs.upload_test_cycle_status(zefir_status='progress')

#Start test
if args.TESTNAME == 'astraevents':
    time_start_script = datetime.now()
    
    astra_events_load_test = AstraEventsLoadTest(rc_name=args.TCV,
                                                 testdir=BASE_PATH,
                                                 vm_count=VM_COUNT,
                                                 vcpu=VCPU_MIN,
                                                 ram=RAM_MIN)

    astra_events_load_test.prepare_vms()
    astra_events_load_test.start_test()
    status = astra_events_load_test.results_processing()
    # astra_events_load_test.vms_destroy()

    # if status:
    #     uzs.upload_test_cycle_status(zefir_status='fail')
    # else:
    #     uzs.upload_test_cycle_status(zefir_status='pass')


    lead_time = get_duration((datetime.now() - time_start_script).total_seconds())
    publisher = astra_events_publisher(
        username=args.USER,
        token=args.TOKEN,
        space=args.SPACE,
        parent_title=args.PPAGE,
        title=args.NPAGE,
        stand_number=args.STAND,
        lead_time=lead_time,
        test_cycle_version=args.TCV,
    )
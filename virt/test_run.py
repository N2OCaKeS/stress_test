from libs.libtests import StealTime, FlexibleIOTester, UnixBench
from virt_conf import LOW, HIGH, REPORT_PATH, ST_RAM, ST_vCPU, IO_DEPTH_1, \
                      IO_DEPTH_128, FIO_RAM, FIO_vCPU, UB_RAM, UB_vCPU
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
if args.TESTNAME == 'stealtime':
    st_no_errors = True
    low_load_test = StealTime(rc_vbox=args.VBOX,
                              vm_count=LOW,
                              testdir=REPORT_PATH,
                              load_type='low',
                              kernel=str(args.TCYC).split('_')[2],
                              vcpu=ST_vCPU,
                              ram=ST_RAM)

    high_load_test = StealTime(rc_vbox=args.VBOX,
                               vm_count=HIGH,
                               testdir=REPORT_PATH,
                               load_type='high',
                               kernel=str(args.TCYC).split('_')[2],
                               vcpu=ST_vCPU,
                               ram=ST_RAM)

    low_load_test.prepare_vms()
    low_load_test.start_test()
    low_load_test.vms_destroy()
    if low_load_test.results_processing() == LOW:
        print('Low load test successfully done')
    else: 
        uzs.upload_test_cycle_status(zefir_status='fail')
        st_no_errors = False

    high_load_test.prepare_vms()
    high_load_test.start_test()
    high_load_test.vms_destroy()
    if high_load_test.results_processing() == HIGH:
        print('High load test successfully done')
    else: 
        uzs.upload_test_cycle_status(zefir_status='fail')
        st_no_errors = False

    info_list()
    uzs.public = True
    #uzs.statistics = True
    if st_no_errors:
        uzs.upload_test_cycle_status(zefir_status='pass')

elif args.TESTNAME == 'fio':
    low_depth_test = FlexibleIOTester(rc_vbox=args.VBOX,
                                      vm_count=2,
                                      testdir=REPORT_PATH,
                                      iodepth=IO_DEPTH_1,
                                      kernel=str(args.TCYC).split('_')[2],
                                      vcpu=FIO_vCPU,
                                      ram=FIO_RAM,
                                      vm_num=1)
    
    high_depth_test = FlexibleIOTester(rc_vbox=args.VBOX,
                                       vm_count=2,
                                       testdir=REPORT_PATH,
                                       iodepth=IO_DEPTH_128,
                                       kernel=str(args.TCYC).split('_')[2],
                                       vcpu=FIO_vCPU,
                                       ram=FIO_RAM,
                                       vm_num=2)

    low_depth_test.prepare_vms()
    low_depth_test.start_test()
    low_depth_test.results_processing()

    high_depth_test.start_test()
    high_depth_test.vms_destroy()
    high_depth_test.results_processing()

    info_list()
    uzs.public = True
    #uzs.statistics = True
    uzs.upload_test_cycle_status(zefir_status='pass')

elif args.TESTNAME == 'unixbench':
    unixbench_test = UnixBench(rc_vbox=args.VBOX,
                               vm_count=1,
                               testdir=REPORT_PATH,
                               kernel=str(args.TCYC).split('_')[2],
                               vcpu=UB_vCPU,
                               ram=UB_RAM,
                               vm_num=1)

    unixbench_test.prepare_vms()
    unixbench_test.start_test()
    unixbench_test.vms_destroy()
    unixbench_test.results_processing()

    

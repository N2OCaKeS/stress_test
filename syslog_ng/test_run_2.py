import argparse
from sng_tests import SNGCheckWriteLogsTest
from libs.libpublic import Public

parser = argparse.ArgumentParser()

parser.add_argument('-vbox', 
                    action='store',
                    required=True,
                    help='vbox name',
                    dest='VBOX')

parser.add_argument('-kernel', 
                    action='store',
                    required=True,
                    help='kernel version',
                    dest='KERNEL')

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
                             '4'],
                    required=True,
                    help='stand num',
                    dest='STAND')

parser.add_argument('-tcv', '--test-cycle-version',
                    action='store',
                    required=True,
                    help='test-cycle-version',
                    dest='TCV')

args = parser.parse_args()

test = SNGCheckWriteLogsTest(vmcount=3, vbox=args.VBOX, kernel=args.KERNEL)
test.prepare()
test.run_test()

public = Public(username=args.USER,
                token=args.TOKEN,
                conf_space=args.SPACE,
                conf_parent_page=args.PPAGE,
                conf_new_page_name=args.NPAGE,
                grade_stand=args.STAND,
                test_cycle_version=args.TCV)

public.run_publish()


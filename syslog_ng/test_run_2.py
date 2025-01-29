import argparse
from sng_tests import SNGCheckWriteLogsTest

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

args = parser.parse_args()

test = SNGCheckWriteLogsTest(vmcount=3, vbox=args.VBOX, kernel=args.KERNEL)
test.prepare()
test.run_test()


import os
from argparse import ArgumentParser

from exb_setup import Dovecot, Exim, CreateMailUsers
from exb_test import SMTPTest
from libs.libtable import Report
from exb_conf import REPORT_PATH
from libs.libpublic import exb_publisher
from libs.zefir import UploaderZC


parser = ArgumentParser()
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
                 grade_stand=args.STAND,
                 test_set=args.TS)
    
    uzs.upload_test_cycle_status('progress')
    if not os.path.exists(REPORT_PATH):
        os.makedirs(REPORT_PATH, mode=0o755)
    d = Dovecot()
    ex = Exim()
    cu = CreateMailUsers(user_count=10)
    for ins in [ex, d, cu]:
        ins.set_configuration()

    test = SMTPTest()
    test.run_test()

    report = Report()
    total_rating = report.get_total_rating()
    publisher = exb_publisher(
        username=args.USER,
        token=args.TOKEN,
        space=args.SPACE,
        parent_title=args.PPAGE,
        title=args.NPAGE,
        total_rating=total_rating,
        stand_number=args.STAND,
        lead_time="",
        test_cycle_version=args.TCV,
    )

    uzs.upload_test_cycle_status(zefir_status='pass')
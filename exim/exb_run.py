import os
from argparse import ArgumentParser

from exb_setup import Dovecot, Exim, CreateMailUsers
from exb_test import SMTPTest, IMAPTest
from libs.libtable import Report

from libs.libpublic import exb_publisher
from libs.zefir import UploaderZC
from exb_conf import REPORT_PATH, MAIL_USERS_QTY_MAX


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

parser.add_argument('-tt', '--type-test',
                    action='store',
                    choices=['smtp', 'imap'],
                    required=False,
                    default='smtp',
                    help='type-test',
                    dest='TT')

args = parser.parse_args()

if __name__ == "__main__":
    # Работа с Zefir
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
    
    # Смена статуса в Zefir
    uzs.upload_test_cycle_status('progress')

    # Создание директории для отчетов
    if not os.path.exists(REPORT_PATH):
        os.makedirs(REPORT_PATH, mode=0o755)

    # Настройка почтовых сервисов + создание пользователей
    d = Dovecot()
    ex = Exim()
    cu = CreateMailUsers(user_count=MAIL_USERS_QTY_MAX)
    for ins in [ex, d, cu]:
        ins.set_configuration()

    # Запуск теста
    if args.TT == 'smtp':
        test = SMTPTest()
    elif args.TT == 'imap':
        test = IMAPTest()
    test.run_test()

    # Обработка результатов и публикация отчета в Confluence
    report = Report(type_test=args.TT)
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
        type_test=args.TT,
    )

    # Смена статуса в Zefir
    uzs.upload_test_cycle_status(zefir_status='pass')
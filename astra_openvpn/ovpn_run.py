import argparse
import traceback

from time import perf_counter
from os import path

from conf import MODIFY
from ovpn.ovpn_vm import Ovpn
from libs.libpublic import ovpn_publisher
from libs.zefir import UploaderZC



parser = argparse.ArgumentParser()
parser.add_argument("--test",
                    choices=["aovpncc"],
                    help="Choose test name.",
                    default="aovpncc",
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
                    choices=[str(i) for i in range(1, 14)],
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

parser.add_argument('-m', '--mode',
                    action='store',
                    choices=MODIFY,
                    default='o',
                    help='VM build mode',
                    dest='MODE')
args = parser.parse_args()
    

def _fmt_duration(seconds: float) -> str:
    total_seconds = int(seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, sec = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{sec:02d}"


if __name__ == "__main__":

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
    uzs.upload_test_cycle_status(zefir_status='progress')


    start_ts = perf_counter()
    zefir_status = 'fail'
    try:
        ovpn = Ovpn()

        if args.TEST == "aovpncc":
            ovpn.build(rc=args.TCV, mode=args.MODE)
            ovpn.provision()
            ovpn.server_settings()
            ovpn.start_test()
            result = ovpn.get_result()
            lead_time_text = _fmt_duration(perf_counter() - start_ts)

            _, preview_path, publish_result = ovpn_publisher(
                username=args.USER,
                token=args.TOKEN,
                title=args.NPAGE,
                stats=result,
                space=args.SPACE,
                parent_title=args.PPAGE,
                lead_time=lead_time_text,
                test_cycle_version=args.TCV,
            )
            print(f"Отчёт отправлен в Confluence, id страниц: {publish_result}")
            print(f"Файл предпросмотра: {preview_path}")
            zefir_status = 'pass'
        else:
            print("Тест не найден")
    except Exception as e:
        print(f"Ошибка при выполнении теста: {e}")
        traceback.print_exc()
    finally:
        uzs.upload_test_cycle_status(zefir_status=zefir_status)

    uzs.statistics = False

if path.isfile('zefir.log'):
    with open('zefir.log', 'r') as r:
        zefir_log = r.read()
        print('\n\n\nZefir-log\n')
        print(zefir_log)
if path.isfile('JIRA_ERROR.log'):
    with open('JIRA_ERROR.log', 'r') as r:
        jira_log = r.read()
        print('\n\n\nJira-log\n')
        print(jira_log)

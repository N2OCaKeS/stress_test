from os import path
import argparse
import traceback
from time import perf_counter
from new_astra_openvpn.conf import MODIFY
from allta import ZefirClient
from ovpn.ovpn_vm import Ovpn
from libs.libpublic import ovpn_publisher

parser = argparse.ArgumentParser()
parser.add_argument("--test",
                    choices=["ovpn"],
                    help="Choose test name.",
                    default="ovpn",
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
parser.add_argument('-pid', '--project-id',
                    action='store',
                    type=int,
                    default=11200,
                    help='Jira/Zefir project id',
                    dest='PID')
parser.add_argument('-juk', '--jira-user-key',
                    action='store',
                    default='JIRAUSER38882',
                    help='Jira user key for status updates',
                    dest='USER_KEY')
args = parser.parse_args()
    

def _fmt_duration(seconds: float) -> str:
    total_seconds = int(seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, sec = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{sec:02d}"


if __name__ == "__main__":

    zefir = ZefirClient(
        basic_auth_header=args.BA,
        project_id=args.PID,
        default_user_key=args.USER_KEY,
        default_folder_tree_id=int(args.FTI),
    )

    def _set_status(status_code: int | str):
        try:
            zefir.set_test_result(
                test_cycle_name=args.TCYC,
                test_case_name=args.TCAS,
                status=status_code,
                folder_tree_id=int(args.FTI),
            )
        except Exception as err:
            print(f"Не удалось установить статус {status_code}: {err}")

    _set_status('progress')

    status = 'fail'
    start_ts = perf_counter()
    try:
        ovpn = Ovpn()

        if args.TEST == "ovpn":
            ovpn.build(rc=args.TCV, mode=args.MODE)
            ovpn.provision()
            ovpn.server_settings()
            ovpn.start_test()
            result = ovpn.get_result()
            lead_time_text = _fmt_duration(perf_counter() - start_ts)

            ovpn_publisher(username=args.USER,
                           token=args.TOKEN,
                           title=args.NPAGE,
                           stats=result,
                           space=args.SPACE,
                           parent_title=args.PPAGE,
                           lead_time=lead_time_text)
            status = 'pass'
        else:
            print("Тест не найден")
    except Exception as e:
        print(f"Ошибка при выполнении теста: {e}")
        traceback.print_exc()
    finally:
        _set_status(status)

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

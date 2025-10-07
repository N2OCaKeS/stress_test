from time import sleep
from datetime import datetime
from argparse import ArgumentParser
from libs.libtable import Report
from libs.libipa import (remote_exec, 
                         remote_put_file, 
                         host_is_available, 
                         remote_cmd, 
                         put_system_info_in_file, 
                         upload_results_to_ftp)
from ipa_conf import HOSTS, USER, INFO_FILENAME, REPORT_PATH
from libs.zefir import UploaderZC
from ipa_tests import AutentificationTest



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
                             '4'],
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
    time_start_script = datetime.now()
    print("Hello")

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
    
    uzs.upload_test_cycle_status('progress')

    """
        Ининциализация КД
    """
    if host_is_available("server") == False:
        sleep(30)
        if host_is_available('server') == False:
            print("\033[91mКД не доступен по ssh!\033[0m")
            exit()

    out = remote_cmd("ip a", HOSTS['server']['ip'])
    print(out)
    sleep(15)
    remote_put_file(HOSTS['server']['ip'], f'/home/u/tokens.json', "/home/u/tokens.json")
    remote_put_file(HOSTS['server']['ip'], f'/home/{USER}/ipa_conf.py', "ipa_conf.py")
    remote_put_file(HOSTS['server']['ip'], f'/home/{USER}/ipa_init_dc.py', "ipa_init_dc.py")
    remote_put_file(HOSTS['server']['ip'], f'/home/{USER}/prepare.sh', "prepare.sh")
    remote_exec(f"sudo bash prepare.sh {args.TCV}", 'server')
    remote_exec("sudo python3 ipa_init_dc.py", 'server')
    
    """
        Инициализация клиента
    """
    # Ждем пока КД перезагрузится
    while host_is_available("server") == False:
        print("\033[91mКД пока не доступен по ssh!\033[0m")
        sleep(300)
    
    if host_is_available("clients") == False:
        sleep(120)
        if host_is_available('clients') == False:
            print("\033[91mКлиент не доступен по ssh!\033[0m")
            exit()

    out_rep = remote_cmd("ip a", HOSTS['clients']['ip'])
    print(out_rep)
    sleep(300)

    # #     out_hostname_replica = remote_cmd("hostname", HOSTS['replica']['ip']).strip("\n")
    # # #     print(out_hostname_replica)
    # # # #     remote_exec(f"sudo astra-freeipa-server-crt --host {out_hostname_replica} --export --48 --pin 12345678 --push u -y", 'server')
    # # #     # """
    # # #     #     #TODO SCP сертификат!!!!!!!!!!!
    # # #     # """
    
    
    # Копируем инициализирующие скрипты по sftp и запускаем
    remote_put_file(HOSTS['clients']['ip'], f'/home/u/tokens.json', "/home/u/tokens.json")
    remote_put_file(HOSTS['clients']['ip'], f'/home/{USER}/ipa_conf.py', "ipa_conf.py")
    remote_put_file(HOSTS['clients']['ip'], f'/home/{USER}/ipa_init_client.py', "ipa_init_client.py")
    remote_exec("sudo python3 ipa_init_client.py", 'clients')
    """
        Запускаем тест
    """

    auth_test = AutentificationTest()
    auth_test.create_users()
    auth_test.run()
    # TODO Дописать info файл

    """
        Создаем отчет
    """
    # TODO дописать title
    report = Report()
    report.create_beauty_table()
    report.create_graph(x=report.user_count, 
                        y=report.sr_znach, 
                        filename="sr_znach",
                        title_graph="Тестовый график",
                        x_label="Количество пользователей", 
                        y_label="Среднее время аутентификации в секундах")
    report.create_graph(x=report.user_count,
                        y=report.proc_errors,
                        filename="proc_errors",
                        title_graph="Тестовый график 2",
                        x_label="Количество пользователей",
                        y_label="Процент невыполненных аутентификаций")
    report.create_graph(x=report.user_count,
                        y=report.value_for_last_proc_delay,
                        filename='values_last',
                        title_graph="Тестовый график 3",
                        x_label="Количество пользователй",
                        y_label="Время аутентификации почти последним пользователем")
    # rating_sr_znach = report.get_rating(report.user_count, report.sr_znach, y_min_for_mathmodel=0, y_max_for_mathmodel=1000)
    # rating_proc_errors = report.get_rating(report.user_count, report.proc_errors, y_min_for_mathmodel=0, y_max_for_mathmodel=1000)
    # rating_last_values = report.get_rating(report.user_count, report.value_for_last_proc_delay, y_min_for_mathmodel=0, y_max_for_mathmodel=1000)
    # total_rating = report.get_total_rating([rating_sr_znach, rating_proc_errors, rating_last_values])
    total_rating = report.get_total_rating()
    print(total_rating)
    put_system_info_in_file(time_start_script, INFO_FILENAME)

    # upload_results_to_ftp(args.TCV, f'{REPORT_PATH}/ipa_report.txt', f'{args.TCYC}_ipa_report.txt')

    uzs.public = True
    uzs.total_rating = total_rating
    #uzs.statistics = True
    uzs.upload_test_cycle_status(zefir_status='pass')

        

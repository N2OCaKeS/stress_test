import os
from time import sleep
from os import path
from argparse import ArgumentParser
from libs.libtable import Report
from libs.libipa import remote_exec, remote_put_file, host_is_available, cmd, remote_cmd
from ipa_conf import HOSTS, USER, REPORT_PATH, DOMAIN

# from libs.libpublic import Public

# from ipa_tests import AutentificationTest

parser = ArgumentParser()

if __name__ == "__main__":
    print("Hello")
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
    remote_put_file(HOSTS['server']['ip'], f'/home/{USER}/ipa_conf.py', "ipa_conf.py")
    remote_put_file(HOSTS['server']['ip'], f'/home/{USER}/ipa_init_dc.py', "ipa_init_dc.py")
    remote_exec("sudo python3 ipa_init_dc.py", 'server')
    
    """
        Инициализация клиента
    """
    # Ждем пока КД перезагрузится
    while host_is_available("server") == False:
        print("\033[91mКД пока не доступен по ssh!\033[0m")
        sleep(1)
    
    if host_is_available("clients") == False:
        sleep(60)
        if host_is_available('clients') == False:
            print("\033[91mКлиент не доступен по ssh!\033[0m")
            exit()

    out_rep = remote_cmd("ip a", HOSTS['clients']['ip'])
    print(out_rep)
    sleep(20)

    # #     out_hostname_replica = remote_cmd("hostname", HOSTS['replica']['ip']).strip("\n")
    # # #     print(out_hostname_replica)
    # # # #     remote_exec(f"sudo astra-freeipa-server-crt --host {out_hostname_replica} --export --48 --pin 12345678 --push u -y", 'server')
    # # #     # """
    # # #     #     #TODO SCP сертификат!!!!!!!!!!!
    # # #     # """
    
    
    # Копируем инициализирующие скрипты по sftp и запускаем
    remote_put_file(HOSTS['clients']['ip'], f'/home/{USER}/ipa_conf.py', "ipa_conf.py")
    remote_put_file(HOSTS['clients']['ip'], f'/home/{USER}/ipa_init_client.py', "ipa_init_client.py")
    remote_exec("sudo python3 ipa_init_client.py", 'clients')
     

    """
        Создание пользователей
    """
    remote_put_file(HOSTS['server']['ip'], f'/home/{USER}/ipa_user_add.py', "ipa_user_add.py")
    remote_exec("python3 ipa_user_add.py", 'server')
    remote_cmd("python3 ipa_user_add.py", HOSTS['server']['ip'])

    """
        Перекидываем тест и запускаем
    """
    remote_put_file(HOSTS['clients']['ip'], f'/home/{USER}/ipa_auth_2.py', "ipa_auth_2.py")
    remote_exec("ulimit -n 100000 && python3 ipa_auth_2.py", 'clients')


    """
        Забираем файл с результатами
    """
    if not path.exists(REPORT_PATH):
        os.mkdir(REPORT_PATH, mode=0o755)
    remote_put_file(host=HOSTS['clients']['ip'], 
                    remote_path=f'/home/{USER}/ipa_report.txt', 
                    local_path=f"{REPORT_PATH}/ipa_report.txt", 
                    local_to_remote=False)
    
    remote_put_file(host=HOSTS['clients']['ip'], 
                    remote_path=f'/home/{USER}/ipa_report_error.txt', 
                    local_path=f"{REPORT_PATH}/ipa_report_error.txt", 
                    local_to_remote=False)

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
    rating_sr_znach = report.get_rating(report.user_count, report.sr_znach, y_min_for_mathmodel=0, y_max_for_mathmodel=1000)
    rating_proc_errors = report.get_rating(report.user_count, report.proc_errors, y_min_for_mathmodel=0, y_max_for_mathmodel=1000)
    rating_last_values = report.get_rating(report.user_count, report.value_for_last_proc_delay, y_min_for_mathmodel=0, y_max_for_mathmodel=1000)
    total_rating = report.get_total_rating([rating_sr_znach, rating_proc_errors, rating_last_values])
    print(total_rating)

        
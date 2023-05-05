import os
from os import path

from argparse import ArgumentParser
from time import sleep, time
from datetime import datetime
from sys import exit

from libs.libtable import Report
from libs.libipa import remote_exec, host_is_available, cmd, remote_cmd, remote_put_file
from ipa_init_client import delete_clients_from_dc, delete_docker_cont
from ipa_conf import HOSTS, USER, DOGTAG, UPPER_LIMITE_CLIENTS, USERS_COUNT, REPORT_PATH

from ipa_tests import EnrollementTest, AutentificationTest

parser = ArgumentParser()

if __name__ == "__main__":

    """
        Ининциализация КД
    """
    # if host_is_available("server") == False:
    #     sleep(30)
    #     if host_is_available('server') == False:
    #         print("\033[91mКД не доступен по ssh!\033[0m")
    #         exit()

    # out = remote_cmd("ip a", HOSTS['server']['ip'])
    # print(out)
    # sleep(15)
    # remote_put_file(HOSTS['server']['ip'], f'/home/{USER}/ipa_conf.py', "ipa_conf.py")
    # remote_put_file(HOSTS['server']['ip'], f'/home/{USER}/ipa_init_dc.py', "ipa_init_dc.py")
    # remote_exec("sudo python3 ipa_init_dc.py", 'server')
    """
        Инициализация реплики
    """
    # Ждем пока КД перезагрузится
    # while host_is_available("server") == False:
    #     print("\031[92mКД пока не доступен по ssh!\033[0m")
    #     sleep(1)

    # if DOGTAG:
    #     if host_is_available("replica") == False:
    #         sleep(30)
    #         if host_is_available('replica') == False:
    #             print("\033[91mРеплика не доступна по ssh!\033[0m")
    #             exit()

    #     out_rep = remote_cmd("ip a", HOSTS['replica']['ip'])
    #     print(out_rep)
    #     sleep(15)
    #     # Копируем инициализирующие скрипты по sftp и запускаем
    #     remote_put_file(HOSTS['replica']['ip'], f'/home/{USER}/ipa_conf.py', "ipa_conf.py")
    #     remote_put_file(HOSTS['replica']['ip'], f'/home/{USER}/ipa_init_replica.py', "ipa_init_replica.py")
    #     remote_exec("sudo python3 ipa_init_replica.py", 'replica')
    
    """
        Ввод клиентов в домен
    """


    #####--------Enrollement TEST------------
    # remote_put_file(HOSTS['server']['ip'], f'/home/{USER}/libscanner.py', "libs/libscanner.py")
    # remote_exec("sudo python3 libscanner.py", 'server')
    
    enroll_test = EnrollementTest([])
    enroll_test.run_test()

    #####--------Enrollement TEST------------

    ##### -------------AUTH TEST----------
    # auth_test = AutentificationTest()
    # c = auth_test.run()
    # print(c)
    # start_time = time()
    # auth_test.create_users()
    # end_time = time()
    # execute_time = end_time - start_time
    # print(f"Время создания {USERS_COUNT} пользователей: {execute_time}")

    ##### ----------------------------

    # auth_test.delete_users()

    # presettings_on_hosts_for_ipa_clients()

    # for ip in HOSTS['hosts-with-clients']['ip']:
    #     print(ip)
    #     images = remote_cmd("sudo docker images", ip)
    #     print(images)
    
    # create_centos_cont()

    # for ip in HOSTS['hosts-with-clients']['ip']:
    #     docker_conts = remote_cmd("sudo docker ps -a", ip)
    #     print(docker_conts)

    # sleep(10)
    # time_start = datetime.now()
    # print("Вводим в домен")
    # init_ipa_client(UPPER_LIMITE_CLIENTS)
    # print(f"Time execution: {datetime.now() - time_start}")

    
    # delete_clients_from_dc(12)
    # delete_docker_cont(12)
    # for ip in HOSTS['hosts-with-clients']['ip']:
    #     docker_conts = remote_cmd("sudo docker ps -a", ip)
    #     print(docker_conts)

    # report = Report()
    # report.create_beauty_table()

        
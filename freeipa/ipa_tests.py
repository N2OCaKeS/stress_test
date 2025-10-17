import os
from os import path
# import random
from time import time, sleep
# from concurrent.futures import ThreadPoolExecutor
# from paramiko import SSHException

from libs.libipa import remote_cmd, remote_put_file, remote_exec
from ipa_conf import USER, HOSTS, REPORT_PATH #, LOWER_LIMITE_CLIENTS, STEP_CLIENTS, UPPER_LIMITE_CLIENTS, USERS_COUNT, 
# from ipa_init_client_enroll import presettings_on_hosts_for_ipa_clients, create_centos_cont, init_ipa_client, delete_clients_from_dc, delete_docker_cont, check_qty_clients


class Results:
    @staticmethod
    def get_results(host):
        """
        Забираем файл с результатами
        """
        if not path.exists(REPORT_PATH):
            os.mkdir(REPORT_PATH, mode=0o755)
        remote_put_file(host=HOSTS[host]['ip'],
                        remote_path=f'/home/{USER}/ipa_report.txt',
                        local_path=f"{REPORT_PATH}/ipa_report.txt",
                        local_to_remote=False)
    
        remote_put_file(host=HOSTS[host]['ip'],
                        remote_path=f'/home/{USER}/ipa_report_error.txt',
                        local_path=f"{REPORT_PATH}/ipa_report_error.txt",
                        local_to_remote=False)


class AutentificationTest():
    def create_users(self):
        """
            Создание пользователей
        """
        remote_put_file(HOSTS['server']['ip'], f'/home/{USER}/ipa_user_add.py', "ipa_user_add.py")
        remote_exec("sudo venv/bin/python3 ipa_user_add.py", 'server')
        # remote_cmd("python3 ipa_user_add.py", HOSTS['server']['ip'])    
        
    def run(self):
        out_hostname_server = remote_cmd("hostname", HOSTS['server']['ip']).strip("\n")
        """
            Перекидываем тест и запускаем
        """
        remote_put_file(HOSTS['clients']['ip'], f'/home/{USER}/ipa_auth_2.py', "ipa_auth_2.py")
        remote_exec(f"ulimit -n 100000 && python3 ipa_auth_2.py -H {out_hostname_server}", 'clients')
        Results.get_results(host='clients')
        
        
class CreateUsersTest():
    def run(self):
        remote_put_file(HOSTS['server']['ip'], f'/home/{USER}/ipa_create_users_test_thr.py', 'ipa_create_users_test_thr.py')
        remote_exec(f"venv/bin/python3 ipa_create_users_test_thr.py", 'server')
        Results.get_results(host='server')


# class EnrollementTest():
    
#     def __init__(self, list_with_qty_clients):
#         self.list_with_qty_clients = list_with_qty_clients

#         enrollement_report = open('enrollement_report.txt', "w")
#         enrollement_report.close()
#         # presettings_on_hosts_for_ipa_clients()

#     def run_test(self):
#         # for clients in range(LOWER_LIMITE_CLIENTS, UPPER_LIMITE_CLIENTS + STEP_CLIENTS, STEP_CLIENTS):
#         # for clients in [10, 25, 50, 100, 200, 300, 400, 500, 1000]:
#         # for clients in [3, 6, 9]:
#         # for clients in [9, 24, 48, 99, 198, 300, 399, 498, 999]:
#         # for clients in [99, 198, 300, 399, 498, 600, 699]:
#         # for clients in [100, 200, 298, 400, 498, 600, 698, 800, 898, 1000]:
#         for clients in [600, 700, 800, 900, 1000]:
#             create_centos_cont(clients)
#             sleep(600)
#             time_start = time()
#             init_ipa_client(clients)
#             time_end = time()
#             losses = check_qty_clients(clients)
#             response_time = time_end - time_start
#             enrollement_report = open('enrollement_report.txt', "a")
#             enrollement_report.write(f"{clients} {response_time} {losses}\n")
#             enrollement_report.close()
#             delete_clients_from_dc(clients)
#             delete_docker_cont(clients)
#             sleep(300)


class CreateUserTest():
    pass


class ApiTest():
    pass
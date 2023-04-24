import random
from time import time, sleep
from concurrent.futures import ThreadPoolExecutor
from paramiko import SSHException

from libs.libipa import remote_cmd
from ipa_conf import HOSTS, LOWER_LIMITE_CLIENTS, STEP_CLIENTS, UPPER_LIMITE_CLIENTS, USERS_COUNT, REPORT_PATH
from ipa_init_client import presettings_on_hosts_for_ipa_clients, create_centos_cont, init_ipa_client, delete_clients_from_dc, delete_docker_cont, check_qty_clients

# class IpaStressTest:
#     def init_clients(qty):
#         pass

#     def run():
#         pass

class EnrollementTest():
    
    def __init__(self, list_with_qty_clients):
        self.list_with_qty_clients = list_with_qty_clients

        enrollement_report = open('enrollement_report.txt', "w")
        enrollement_report.close()
        # presettings_on_hosts_for_ipa_clients()

    def run_test(self):
        for clients in range(LOWER_LIMITE_CLIENTS, UPPER_LIMITE_CLIENTS + STEP_CLIENTS, STEP_CLIENTS):
        # for clients in [10, 25, 50, 100]:
            create_centos_cont(clients)
            sleep(30)
            time_start = time()
            init_ipa_client(clients)
            time_end = time()
            losses = check_qty_clients(clients)
            response_time = time_end - time_start
            enrollement_report = open('enrollement_report.txt', "a")
            enrollement_report.write(f"{clients} {response_time} {losses}\n")
            enrollement_report.close()
            delete_clients_from_dc(clients)
            delete_docker_cont(clients)


class AutentificationTest():
    def create_users(self, qty=USERS_COUNT):
        for id_user in range(1, qty+1):
            remote_cmd(command=f'echo -e 12345678 | ipa user-add tester{id_user} --first="Test{id_user}" --last="Testov{id_user}" --password-expiration=2025-10-12Z --password', host=HOSTS['server']['ip'])

    def delete_users(self, qty=USERS_COUNT):
        for id_user in range(1, qty+1):
            remote_cmd(command=f"ipa user-del tester{id_user}", host=HOSTS['server']['ip'])

    def run(self):
        count_failed = 0
        def auth():
            nonlocal count_failed
            user_id= random.randint(1, USERS_COUNT)
            start_time = time()
            out = remote_cmd(command="whoami", host=HOSTS['hosts-with-clients']['ip'][0], user=f"tester{user_id}", passwd="12345678")
            if not out:
                count_failed += 1
            print(out)
            end_time = time()
            exec_time = end_time - start_time
            print(exec_time)

        with ThreadPoolExecutor(max_workers=200) as executor:
            for i in range(200):
                executor.submit(auth)
        return count_failed 


class CreateUserTest():
    pass


class ApiTest():
    pass
from argparse import ArgumentParser
from time import sleep, time
from datetime import datetime
from sys import exit

from libs.libipa import remote_exec, host_is_available, cmd, remote_cmd, remote_put_file
from ipa_init_client import create_centos_cont, init_ipa_client, delete_ipa_client
from ipa_conf import HOSTS, USER

parser = ArgumentParser()

if __name__ == "__main__":

    """
        Ининциализация КД
    """
    if host_is_available("server") == False:
        sleep(30)
        if host_is_available('server') == False:
            print("\031[92mКД не доступен по ssh!\033[0m")
            exit()

    out = remote_cmd("ip a", HOSTS['server']['ip'])
    print(out)
    sleep(15)
    remote_put_file(HOSTS['server']['ip'], f'/home/{USER}/ipa_conf.py', "ipa_conf.py")
    remote_put_file(HOSTS['server']['ip'], f'/home/{USER}/ipa_init_dc.py', "ipa_init_dc.py")
    remote_exec("sudo python3 ipa_init_dc.py", 'server')
    
    """
        Инициализация реплики
    """
    # Ждем пока КД перезагрузится
    while host_is_available("server") == False:
        sleep(1)
    
    if host_is_available("replica") == False:
        sleep(30)
        if host_is_available('replica') == False:
            print("\031[92mРеплика не доступна по ssh!\033[0m")
            exit()

    out_rep = remote_cmd("ip a", HOSTS['replica']['ip'])
    print(out_rep)
    sleep(15)
    # # Копируем инициализирующие скрипты по sftp и запускаем
    remote_put_file(HOSTS['replica']['ip'], f'/home/{USER}/ipa_conf.py', "ipa_conf.py")
    remote_put_file(HOSTS['replica']['ip'], f'/home/{USER}/ipa_init_replica.py', "ipa_init_replica.py")
    remote_exec("sudo python3 ipa_init_replica.py", 'replica')
    
    """
        Ввод клиентов в домен
    """
    images = remote_cmd("sudo docker images", HOSTS['client']['ip'])
    print(images)
    
    create_centos_cont()

    docker_conts = remote_cmd("sudo docker ps -a", HOSTS['client']['ip'])
    print(docker_conts)

    sleep(300)
    time_start = datetime.now()
    print("Вводим в домен")
    init_ipa_client()
    print(f"Time execution: {datetime.now() - time_start}")

    # delete_ipa_client()
    # docker_conts = remote_cmd("sudo docker ps -a", HOSTS['client']['ip'])
    # print(docker_conts)

    
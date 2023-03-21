from argparse import ArgumentParser
from time import sleep, time
from libs.libipa import remote_exec, host_is_available, cmd, remote_cmd, remote_put_file
from ipa_init_client import create_centos_cont, update_repo_in_docker_conts, install_packages_in_docker_conts, init_ipa_client, delete_ipa_client
from ipa_conf import HOSTS, USER

parser = ArgumentParser()

if __name__ == "__main__":
    """
        Ининциализация КД
    """
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
    #     sleep(1)

    # out_rep = remote_cmd("ip a", HOSTS['replica']['ip'])
    # print(out_rep)
    # sleep(15)
    # # Копируем инициализирующие скрипты по sftp и запускаем
    # remote_put_file(HOSTS['replica']['ip'], f'/home/{USER}/ipa_conf.py', "ipa_conf.py")
    # remote_put_file(HOSTS['replica']['ip'], f'/home/{USER}/ipa_init_replica.py', "ipa_init_replica.py")
    # remote_exec("sudo python3 ipa_init_replica.py", 'replica')
    """
        Ввод клиентов в домен
    """
    ###
    # 1 Запустить докер контейнеры
    # 2 установить необходимые пакеты
    # 3 вводить клиентов в домен командой astra-freeipa-client
    
    ##### ПРЕДВАРИТЕЛЬНЫЕ НАСТРОЙКИ
    #1 Подключаемся по ssh на брестовую тачку и запускаем докер контейнеры
    images = remote_cmd("sudo docker images", HOSTS['client']['ip'])
    print(images)

    
    create_centos_cont(100)

    docker_conts = remote_cmd("sudo docker ps -a", HOSTS['client']['ip'])
    print(docker_conts)

    # update_repo_in_docker_conts(25)
    # install_packages_in_docker_conts(25, ["ssh", "astra-freeipa-client"])
    # print("Ждем установки")
    sleep(300)
    print("Вводим в домен")
    init_ipa_client(100)

    # delete_ipa_client(100)
    # docker_conts = remote_cmd("sudo docker ps -a", HOSTS['client']['ip'])
    # print(docker_conts)

    
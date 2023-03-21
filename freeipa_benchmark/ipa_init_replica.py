#### Инициализация реплики

import subprocess

# from libs.libipa import cmd
from time import sleep
from ipa_conf import DOGTAG, DC_PASSWORD, HOSTS, DOMAIN


def cmd(command):
    ret_code = subprocess.run(command, shell=True).returncode
    return ret_code

def initialization_freeipa_replica():
    
    cmd("apt install resolvconf astra-freeipa-client astra-freeipa-server -y")
    
    if DOGTAG:
        """
            Добавление расширенного репозитория
        """
        with open("/etc/apt/sources.list", "r+") as file_sl:
            for line in file_sl:
                if "base" in line:
                    template_repo = line.replace('base', 'extended')
                    file_sl.write(template_repo)
        """
            Обновление списка пакетов
        """    
        cmd("apt update")
        
        """
            Установка пакета dogtag-pki
        """    
        inst_dogtag = cmd("apt install dogtag-pki -y")
        if inst_dogtag is not 0:
            cmd("aptitude install dogtag-pki -y")
    
        """
            Настройка DNS
        """
        # Удаление старых параметров из /etc/network/interfaces
        file_network = open("/etc/network/interfaces", "r")
        network_settings = file_network.readlines()
        file_network.close()

        for line in network_settings[::]:
            if "dns-nameservers" in line:
                network_settings.remove(line)
            if "dns-domain" in line:
                network_settings.remove(line)
        
        # Запись в /etc/network/interfaces новые параметры dns
        network_settings.append(f"dns-nameservers {HOSTS['server']['ip']}\n")
        network_settings.append(f"dns-domain {DOMAIN}\n")
        
        file_network = open("/etc/network/interfaces", "w")
        file_network.writelines(network_settings)
        file_network.close()
        
        # Перезапуск сервиса networking
        cmd("systemctl restart networking.service")

        """
            Ввод клиента в домен
        """
        cmd(f"astra-freeipa-client -y -p {DC_PASSWORD}")
        sleep(10)
        
        """
            Инициализация реплики
        """
        cmd(f"echo -e yes | astra-freeipa-replica --dogtag -y -p {DC_PASSWORD}")
        
    else:
        pass
    
    """
        Перезапуск контроллера домена
    """
    print("\033[92mПЕРЕЗАПУСК реплики...\033[0m")
    cmd("reboot")


if __name__ == "__main__":
    initialization_freeipa_replica()
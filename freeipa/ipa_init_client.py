#### Инициализация реплики

import subprocess

# from libs.libipa import cmd
from time import sleep
from ipa_conf import REPLICA, DC_PASSWORD, HOSTS, DOMAIN #EXT_REPO


def cmd(command, ret_c=True):
    if ret_c:
        ret_code = subprocess.run(command, shell=True).returncode
        return ret_code
    else:
        output = subprocess.run(command, 
                                shell=True, 
                                stdout=subprocess.PIPE, 
                                encoding='utf-8').stdout.strip('\n')
        return output

def change_defualt_kernel():
    command_search = "sudo cat /boot/grub/grub.cfg | grep menuentry_id | awk '{print $17}' | grep 5.15 | tr -d \"'\" | grep generic"
    
    kernel_search = cmd(command=command_search, ret_c=False)
    command_change = f"sudo sed -i 's/GRUB_DEFAULT=.*/GRUB_DEFAULT={kernel_search}/' /etc/default/grub && sudo update-grub"
    kernel_change = cmd(command=command_change)

def initialization_freeipa_client():

    """
        Обновление списка пакетов и установка клиента фриипы
    """    
    cmd("apt update")
    
    cmd("apt install -y astra-freeipa-client")
    
    """
        Костыль для временной замены записей DNS (Чтобы не перезагружая ввести в домен)
    """
    with open('/etc/resolv.conf', 'w') as file_resolv:
        file_resolv.write(f"nameserver {HOSTS['server']['ip']}\n")
        file_resolv.write(f"search {DOMAIN}\n")

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
    # cmd("systemctl restart networking.service")

    """
        Перевод hostname в нижний регистр
    """
    hostname_file = open("/etc/hostname", "r")
    hostname = hostname_file.readline()
    hostname_file.close()
    cmd(f"hostnamectl set-hostname {hostname.lower()}")
    # hostname_file = open("/etc/hostname", "w")
    # hostname_file.write(hostname.lower())
    # hostname_file.close()
    file_hosts = open("/etc/hosts", "r")
    temp = file_hosts.readlines()
    for ind, line in enumerate(temp[::]):
        if hostname in line:
            new_line = line.replace(hostname, hostname.lower())
            temp.remove(line)
            temp.insert(ind, new_line)
    file_hosts.close()
    file_hosts = open("/etc/hosts", "w")
    file_hosts.writelines(temp)
    file_hosts.close()

    """
        Ввод клиента в домен
    """
    cmd(f"astra-freeipa-client -y -p {DC_PASSWORD}")
    """
        Перезапуск контроллера домена
    """
    print("\033[92mПЕРЕЗАПУСК клиента333...\033[0m")
    sleep(10)
    cmd("reboot")


if __name__ == "__main__":
    change_defualt_kernel()
    initialization_freeipa_client()
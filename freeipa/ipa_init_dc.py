import subprocess
# from libs.libipa import cmd
from os import linesep
from ipa_conf import REPLICA, DOMAIN, DC_PASSWORD

def check_output_command(command, out=None):
    result = subprocess.Popen([command], shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    output, errors = result.communicate()
    output = linesep.join([s for s in output.splitlines() if s])
    errors = linesep.join([s for s in errors.splitlines() if s])
    if errors == "":
        return output
    elif out != None:
        return errors + output
    else:
        return errors

def cmd(command):
    ret_code = subprocess.run(command, shell=True).returncode
    return ret_code

def initialization_freeipa_server():
    """
        Удаление домена перед его инициализацией
    """
    # cmd("astra-freeipa-server -U")
    
    """
        Обновление списка пакетов
    """    
    cmd("sudo install -d -m 0755 /etc && printf '%s\\n' '[global]' 'index-url = http://allta.devos.astralinux.ru:3141/root/release' 'trusted-host = allta.devos.astralinux.ru' | sudo tee /etc/pip.conf >/dev/null && sudo chmod 0644 /etc/pip.conf")
    cmd("sudo apt update -y")
    cmd("sudo apt install python3-venv build-essential python3-dev python3-pip gcc -y")
    cmd("sudo apt install libkrb5-dev -y")
    """
        Установка пакетов astra-freeipa-server
    """    
    cmd("sudo apt install astra-freeipa-server -y")

    """
        Перевод hostname в нижний регистр
    """
    hostname_file = open("/etc/hostname", "r")
    hostname = hostname_file.readline()
    hostname_file.close()
    # hostname_file = open("/etc/hostname", "w")
    cmd(f"hostnamectl set-hostname {hostname.lower()}")
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
        Установка либы для создания пользователей
    """
    # if int(check_output_command("python3 --version").split()[1].split(".")[1]) >= 11:
    #     cmd("sudo pip3 install python-freeipa --break-system-packages")
    # else:
    #     cmd("sudo pip3 install python-freeipa")
    cmd("sudo python3 -m venv venv")
    cmd("venv/bin/pip3 install python-freeipa requests-gssapi gssapi")
    
    """
        Удаление 10.177.128.198 из /etc/resolv.conf и /etc/network/interfaces
    """
    with open('/etc/resolv.conf', 'r') as file_resolv:
        resolv_lines = file_resolv.readlines()
    resolv_lines = [line for line in resolv_lines if 'nameserver 10.177.128.198' not in line]
    with open('/etc/resolv.conf', 'w') as file_resolv:
        file_resolv.writelines(resolv_lines)

    file_network = open("/etc/network/interfaces", "r")
    network_settings = file_network.readlines()
    file_network.close()
    for i, line in enumerate(network_settings):
        if "dns-nameservers" in line:
            parts = line.split()
            parts = [p for p in parts if p != '10.177.128.198']
            network_settings[i] = ' '.join(parts) + '\n'
    file_network = open("/etc/network/interfaces", "w")
    file_network.writelines(network_settings)
    file_network.close()

    """
        Инициализация домена
    """
    # cmd(f"astra-freeipa-server --dogtag -p {DC_PASSWORD} -d {DOMAIN} -y")
    # cmd(f"astra-freeipa-server --ssl -p {DC_PASSWORD} -d {DOMAIN} -y")
    cmd(f'astra-freeipa-server --ssl -p {DC_PASSWORD} -d {DOMAIN} -y --par "--allow-zone-overlap"')

    file_hosts = open("/etc/hosts", "a")
    file_hosts.write("10.177.103.10\tallta.devos.astralinux.ru\n")
    file_hosts.close()
    """
        Перезапуск контроллера домена
    """
    print("\033[92mПЕРЕЗАПУСК КД...\033[0m")
    cmd("reboot")


if __name__ == "__main__":
    initialization_freeipa_server()

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
    cmd("sudo apt update -y")
    cmd("sudo apt install python3-pip -y")
    if int(check_output_command("python3 --version").split()[1].split(".")[1]) >= 11:
        cmd("sudo pip3 install python-freeipa --break-system-packages")
    else:
        cmd("sudo pip3 install python-freeipa")
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
        Инициализация домена
    """    
    # cmd(f"astra-freeipa-server --dogtag -p {DC_PASSWORD} -d {DOMAIN} -y")
    cmd(f"astra-freeipa-server --ssl -p {DC_PASSWORD} -d {DOMAIN} -y")

    file_hosts = open("/etc/hosts", "a")
    file_hosts.write("10.77.103.10\tallta.devos.astralinux.ru\n")
    file_hosts.close()
    """
        Перезапуск контроллера домена
    """
    print("\033[92mПЕРЕЗАПУСК КД...\033[0m")
    cmd("reboot")


if __name__ == "__main__":
    initialization_freeipa_server()
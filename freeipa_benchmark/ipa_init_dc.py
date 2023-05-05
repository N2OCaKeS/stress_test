import subprocess
# from libs.libipa import cmd
from ipa_conf import DOGTAG, DOMAIN, DC_PASSWORD, EXT_REPO

def cmd(command):
    ret_code = subprocess.run(command, shell=True).returncode
    return ret_code

def initialization_freeipa_server():
    """
        Удаление домена перед его инициализацией
    """
    # cmd("astra-freeipa-server -U")
    
    """
        Установка контроллера домена
    """
    if DOGTAG:
        """
            Добавление расширенного репозитория
        """
        with open("/etc/apt/sources.list", "a") as file_sl:
            file_sl.write(f'{EXT_REPO} \n')
            # for line in file_sl:
                # if "base" in line:
                #     template_repo = line.replace('base', 'extended')
                #     file_sl.write(template_repo)
        """
            Обновление списка пакетов
        """    
        cmd("apt update -y")

        """
            Установка пакетов dogtag-pki astra-freeipa-client
        # """    
        inst_dogtag = cmd("apt install dogtag-pki -y")
        if inst_dogtag is not 0:
            cmd("aptitude install dogtag-pki -y")

        cmd("apt install astra-freeipa-server -y")
    
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
        cmd(f"astra-freeipa-server --dogtag -p {DC_PASSWORD} -d {DOMAIN} -y")
    else:
        pass
    
    """
        Перезапуск контроллера домена
    """
    print("\033[92mПЕРЕЗАПУСК КД...\033[0m")
    cmd("reboot")


if __name__ == "__main__":
    initialization_freeipa_server()
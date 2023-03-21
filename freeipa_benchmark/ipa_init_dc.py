import subprocess
# from libs.libipa import cmd
from ipa_conf import DOGTAG, DOMAIN, DC_PASSWORD

def cmd(command):
    ret_code = subprocess.run(command, shell=True).returncode
    return ret_code

def initialization_freeipa_server():
    """
        Удаление домена перед его инициализацией
    """
    cmd("astra-freeipa-server -U")
    
    """
        Установка контроллера домена
    """
    cmd("apt install astra-freeipa-server -y")
    
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
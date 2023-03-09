#### Инициализация реплики

from libs.libipa import cmd
from ipa_conf import DOGTAG, DC_PASSWORD

def initialization_freeipa_replica():
    # 1 Установить astra-freeipa-client astra-freeipa-server -y
    cmd("apt install astra-freeipa-client astra-freeipa-server -y")
    # 2 Добавить extended репу и установить dogtag-pki
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
    
        # 3 Ввод клиента в домен astra-freeipa-client -y -p {kd_pass}
        cmd(f"astra-freeipa-client -y -p {DC_PASSWORD}")
        # 4 astra-freeipa-replica --dogtag -y -p {kd_pass}
        cmd(f"astra-freeipa-replica --dogtag -y -p {DC_PASSWORD}")
        print()
    else:
        pass
    
    """
        Перезапуск контроллера домена
    """
    print("\033[92mПЕРЕЗАПУСК реплики...\033[0m")
    cmd("reboot")


if __name__ == "__main__":
    initialization_freeipa_replica()
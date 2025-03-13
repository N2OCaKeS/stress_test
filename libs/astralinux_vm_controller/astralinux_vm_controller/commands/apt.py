from os import system
from vbox.vbox import vbox

class AptManager:
    @staticmethod
    def install(packages, vms: list, vms_group: dict, vms_date: dict):
        """
        Устанавливает указанные пакеты через apt-get install.

        Args:
            packages (str или list): название пакета или список пакетов для установки.
        """
        if isinstance(packages, list):
            pkg_str = " ".join(packages)
        else:
            pkg_str = packages
        command = f"sudo apt-get install -y {pkg_str}"
        print(f"Выполняется команда: {command}")
        return vbox.execute(vms, vms_group, vms_date, {'install': {'command': command}}, 'username', 'password')
    
    @staticmethod
    def remove(packages, vms: list, vms_group: dict, vms_date: dict):
        """
        Удаляет указанные пакеты через apt-get remove.

        Args:
            packages (str или list): название пакета или список пакетов для удаления.
        """
        if isinstance(packages, list):
            pkg_str = " ".join(packages)
        else:
            pkg_str = packages
        command = f"sudo apt-get remove -y {pkg_str}"
        print(f"Выполняется команда: {command}")
        return vbox.execute(vms, vms_group, vms_date, {'remove': {'command': command}}, 'username', 'password')
from os import system

class AptManager:
    @staticmethod
    def install(packages, vms: list, vms_date: dict):
        """
        Устанавливает указанные пакеты через apt-get install.

        Args:
            packages (str или list): название пакета или список пакетов для установки.
        """
        # if isinstance(packages, list):
        #     pkg_str = " ".join(packages)
        # else:
        #     pkg_str = packages
        # command = f"sudo apt-get install -y {pkg_str}"
        # print(f"Выполняется команда: {command}")
        # return system.cmd(command)
    
    @staticmethod
    def remove(packages):
        """
        Удаляет указанные пакеты через apt-get remove.

        Args:
            packages (str или list): название пакета или список пакетов для удаления.
        """



        # if isinstance(packages, list):
        #     pkg_str = " ".join(packages)
        # else:
        #     pkg_str = packages
        # command = f"sudo apt-get remove -y {pkg_str}"
        # print(f"Выполняется команда: {command}")
        # return system.cmd(command)
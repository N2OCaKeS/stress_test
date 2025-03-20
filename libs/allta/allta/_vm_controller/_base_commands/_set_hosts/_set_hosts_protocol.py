from typing import Protocol, Dict, Any

class _HostsManagerProtocol(Protocol):
    def __call__(self, domain: str, vm_dates: dict, 
                 username: str = "u", password: str = "1",
                 task_name: str = "Set /etc/hosts") -> Dict[str, Any]:
        """
        Обновляет файл /etc/hosts на всех ВМ через SSH.

        Формат файла /etc/hosts для каждой ВМ:
            127.0.0.1       localhost
            127.0.0.1       {текущий_хост}.{domain}
            {ip_bridge}     {vm}.{domain}      {vm}

        :param domain: Домен для формирования FQDN, например "example.com".
        :param vm_dates: Данные по виртуальным машинам.
        :param username: Имя пользователя для SSH.
        :param password: Пароль для SSH.
        :param task_name: Имя задачи для логирования.
        :return: Словарь с результатами выполнения команды для каждой ВМ.
        """
        ...

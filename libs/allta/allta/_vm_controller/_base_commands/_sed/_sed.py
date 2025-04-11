from ...._decorators.Decorators import BaseDecorators
from ..._libs._ssh_command import _SSH_Command
from time import sleep
import concurrent.futures

class _Sed:
    @staticmethod
    @BaseDecorators.trycorator
    def sed(sed_conf: dict, vms_dates: dict, groups: dict, username='u', password='1', task_name=None): # TODO Реализовать многопоточность для групп хостов
        """
        Выполняет замену текста на удалённых хостах согласно переданным настройкам, используя многопоточность.

        Для каждого элемента из sed_conf:
         - Если ключ начинается с "g_", то команда выполняется на всех хостах из соответствующей группы.
         - Иначе ключ считается именем отдельного хоста.

        Команда замены формируется в виде:
            sed -i 's/<old>/<new>/g' <path>

        :param sed_conf: Словарь с параметрами sed, например:
                         {
                           'suac': {
                               'path': '/etc/postgresql/15/main/pg_hba.conf',
                               'old':'# IPv4 local connections:',
                               'new':'' 
                           },
                           'g_database': { ... }  # если ключ начинается с "g_", то команда выполнится для группы
                         }
        :param vms: Список имен хостов.
        :param vms_dates: Словарь с данными для подключения к хостам.
        :param groups: Словарь групп, где ключ – имя группы, а значение – список хостов, например:
                       {'database': ['suac', 'fiac']}
        :param username: Имя пользователя для SSH-подключения (по умолчанию 'u').
        :param password: Пароль для SSH-подключения (по умолчанию '1').
        :param task_name: Имя задачи для логирования.
        :return: Список результатов выполнения команды для каждого хоста.
        """
        results = []
        tasks = []

        def build_sed_command(old: str, new: str, path: str, delimiter: str = "@") -> str:
            old_escaped = old.replace('"', '\\"').replace("\\", "\\\\")
            new_escaped = new.replace('"', '\\"').replace("\\", "\\\\")
            if delimiter in old_escaped or delimiter in new_escaped:
                old_escaped = old_escaped.replace(delimiter, f"\\{delimiter}")
                new_escaped = new_escaped.replace(delimiter, f"\\{delimiter}")
            return f'sudo sed -i "s{delimiter}{old_escaped}{delimiter}{new_escaped}{delimiter}g" {path}'

        with concurrent.futures.ThreadPoolExecutor() as executor:
            for target, configs in sed_conf.items():
                # Убедимся, что это список конфигураций
                if not isinstance(configs, list):
                    configs = [configs]

                if target.startswith('g_'):
                    group_name = target[2:]
                    hosts = groups.get(group_name, [])
                else:
                    hosts = [target]

                for host in hosts:
                    if host not in vms_dates:
                        print(f"Хост {host} не найден в vms_dates.")
                        continue

                    for conf in configs:
                        command = build_sed_command(conf['old'], conf['new'], conf['path'])
                        future = executor.submit(
                            _SSH_Command.cmd,
                            host,
                            command,
                            vms_dates,
                            username=username,
                            password=password,
                            task_name=task_name
                        )
                        tasks.append(future)
                        sleep(0.1)

            for future in concurrent.futures.as_completed(tasks):
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    print(f"Ошибка выполнения задачи: {e}")

        return results

from ..._libs._ssh_comand import _SSH_Command
import concurrent.futures

class _Sed:
    @staticmethod
    def sed(sed_conf: dict, vms_dates: dict, groups: dict, username='u', password='1', task_name=None):
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

        # Используем ThreadPoolExecutor для параллельного выполнения задач
        with concurrent.futures.ThreadPoolExecutor() as executor:
            for key, conf in sed_conf.items():
                # Формируем команду замены
                command = f"sed -i 's/{conf['old']}/{conf['new']}/g' {conf['path']}"
                # Если ключ начинается с "g_", то это группа хостов
                if key.startswith('g_'):
                    group_name = key[2:]
                    if group_name in groups:
                        for host in groups[group_name]:
                            if host in vms_dates:
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
                            else:
                                print(f"Хост {host} не найден в vms_dates.")
                    else:
                        print(f"Группа {group_name} не найдена в groups.")
                else:
                    # Если ключ не начинается с "g_", то это конкретный хост
                    host = key
                    if host in vms_dates:
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
                    else:
                        print(f"Хост {host} не найден в vms_dates.")

            # Ожидаем завершения всех задач и собираем результаты
            for future in concurrent.futures.as_completed(tasks):
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    print(f"Ошибка выполнения задачи: {e}")

        return results

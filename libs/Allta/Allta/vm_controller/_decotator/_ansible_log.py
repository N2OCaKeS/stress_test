import sys

def ansible_logger(func):
    """
    Декоратор для логирования результата выполнения функции по аналогии с Ansible.
    Все параметры для логирования берутся из словаря, возвращаемого функцией.

    Ожидается, что функция возвращает словарь со следующими ключами:
      - host: имя хоста (если отсутствует, используется 'unknown')
      - task_name: имя задачи (если отсутствует, используется 'unknown')
      - output: вывод команды
      - status: статус выполнения (например, 'error', 'success', 'CHANGED')
      - command: выполненная команда (если отсутствует, используется 'Команда не задана')
    """
    def wrapper(*args, **kwargs):
        try:
            result = func(*args, **kwargs)
            
            # Извлечение параметров из результата
            host = result.get('host', 'unknown')
            task_name = result.get('task_name', 'unknown')
            command_output = result.get('output', '')
            status = result.get('status', 'OK')
            executed_command = result.get('command', 'Команда не задана')
            
            # Приведение статуса к нужному виду
            status_lower = str(status).lower()
            if status_lower == 'error':
                display_status = 'FATAL'
            elif status_lower == 'success':
                display_status = 'OK'
            else:
                display_status = str(status).upper()
            
            # Определение символа для границы логирования
            border_char = '#' if display_status == 'FATAL' else '*'
            border_line = border_char * 66

            # Формирование строки лога
            log_entry = (
                f"TASK [{task_name}: {host}] {border_line}\n"
                f"STATUS [{display_status}]\n"
                f"COMMAND: {executed_command}\n"
                f"{command_output}\n"
                f"{border_line}\n\n\n"
            )
            
            # Запись лога в файл
            try:
                with open('test.log', 'a', encoding='utf-8') as log_file:
                    log_file.write(log_entry)
                print(f"Лог задачи {task_name} на {host} успешно записан")
            except IOError as io_error:
                print(f"Ошибка записи в лог {task_name} на {host}: {io_error}")
            
            # Если статус не входит в разрешённые, завершаем выполнение
            allowed_statuses = ['CHANGED', 'OK']
            if display_status not in allowed_statuses:
                sys.exit(1)
            
            return result

        except Exception as error:
            print(f"Ошибка в декораторе ansible_logger: {error}")
            sys.exit(1)
    
    return wrapper

import sys

def ansible(func):
    def wrapper(*args, **kwargs):
        try:
            result = func(*args, **kwargs)
            # Если host и task_name не переданы через kwargs, пробуем взять их из результата
            host = kwargs.get('host', result.get('host', 'unknown'))
            task_name = kwargs.get('task_name', result.get('task_name', 'unknown'))
            command_output = result.get('output', '')
            status = result.get('status', 'OK')
            executed_command = result.get('command', 'Команда не задана')

            if status.lower() == 'error':
                display_status = 'FATAL'
            elif status.lower() == 'success':
                display_status = 'OK'
            else:
                display_status = status.upper()

            allowed_statuses = ['CHANGED', 'OK']
            border_char = '#' if display_status == 'FATAL' else '*'
            border_line = border_char * 66

            log_entry = (
                f"TASK [{task_name}: {host}] {border_line}\n"
                f"STATUS [{display_status}]\n"
                f"COMMAND: {executed_command}\n"
                f"{command_output}\n"
                f"{border_line}\n\n\n"
            )

            try:
                with open('test.log', 'a') as log_file:
                    log_file.write(log_entry)
                print(f"Лог задачи {task_name} на {host} успешно записан")
            except IOError as e:
                print(f"Ошибка записи в лог {task_name} на {host}: {str(e)}")

            if display_status not in allowed_statuses:
                sys.exit(1)

            return result

        except Exception as e:
            print(f"Ошибка в ansible-декораторе: {str(e)}")
            sys.exit(1)

    return wrapper
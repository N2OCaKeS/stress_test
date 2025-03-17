def ansible(func):
    def wrapper(*args, **kwargs):
        try:
            result = func(*args, **kwargs)
            host = kwargs.get('host', 'unknown')
            task_name = kwargs.get('task_name', 'unknown')
            command_output = result.get('output', '')
            

            log_entry = (
                f"TASK [{task_name}: {host}] **********************************************************\n"
                f"{command_output}\n"
                f"**********************************************************\n\n\n"
            )

            try:
                with open('test.log', 'a') as log_file:
                    log_file.write(log_entry)
                print(f"Лог задачи {task_name} на {host} успешно записан")
            except IOError as e:
                print(f"Ошибка записи в лог {task_name} на {host}: {str(e)}")

            return result

        except Exception as e:
            print(f"Ошибка в ansible-декораторе: {str(e)}")
            return {'output': str(e), 'status': 'error'}

    return wrapper

# Description: Decorators for Astralinux VM Controller
def trycorator(function):
    def wrapper(*args, **kwargs):
        try: 
            function(*args, **kwargs)
        except Exception as e:
            print(f'Function: {function.__name__}\nError is: {str(type(e).__name__)}\nMessage: {str(e)}')

    return wrapper

def log_task(func):
    def wrapper(*args, **kwargs):
        result = func(*args, **kwargs)
        host = kwargs.get('host')
        task_name = kwargs.get('task_name')
        command_output = result.get('output')
        with open('test.log', 'a') as log_file:
            log_file.write(f"TASK [{task_name}: {host}] **********************************************************\n{command_output}\n **********************************************************\n\n\n")
        return result
    return wrapper


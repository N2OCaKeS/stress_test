
import subprocess
import signal

from abc import ABC, abstractmethod
from typing import Union, Tuple
from functools import wraps
from os import linesep

from osb_logger import log



class Test(ABC):
    
    """
    Абстрактный конвейер
    """

    @abstractmethod
    def start_test(cls) -> Union[str, None]:
        """Запуск серии тестов с использованием бенчмарка 'X'"""
        pass
        
    @abstractmethod
    def get_results(cls) -> Union[str, None]:
        """Сбор результатов тестирования"""
        pass



def status_check(method):

    """
    Декоратор, который выводит сообщение с статусом, 
    сигнализирующем об успешности выполнения метода
    """

    @wraps(method)
    def wrapper(self, *args, **kwargs):
        log.info(f'Вызван метод "{method.__name__}"')
        try:
            result, status  = method(self, *args, **kwargs)
            if not result and not status:
                log.info(f'Выполнение "{method.__name__}" отменено, установлен статут False\n')
            elif not status:
                log.error(f'При выполнении "{method.__name__}" произошла ошибка\n')
                log.error(result)
                log.info('End logging\n')
                exit(1)
            else:   
                log.info(result)
                log.info(f'Метод "{method.__name__}" успешно выполнен\n')
            return result
        except Exception as e:
            log.error(f'\nERROR:\nMethod: {method.__name__}\nError is: {str(type(e).__name__)}\nMessage: {str(e)}')
            log.info('End logging\n')
            exit(1)
    return wrapper



class system:
    
    """
    Обращение к системе
    """

    @staticmethod
    def command(command: str, returncode=None) -> Tuple[str, bool]:
        """
        Вывод в терминал/лог после завершения команды
        """
        result = subprocess.Popen([command], shell=True, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, universal_newlines=True, text=True)
        result.wait()
        output, errors = result.communicate()
        output = linesep.join([s for s in output.splitlines() if s])
        errors = linesep.join([s for s in errors.splitlines() if s])
        code = result.returncode == 0
        if returncode:
            if not errors:
               return output, code
            else:
               return errors, code
        else:
            if not errors:
                 return output, True
            else:
                 return errors, False
            

    @staticmethod
    def leave_command(command: str, returncode=None) -> Tuple[str, bool]:
        """
        Построчный вывод в терминал/лог
        """
        log.info(f"Выполняется команда: {command}")

        output_lines = []
        error_lines = []

        process = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, bufsize=1, universal_newlines=True)
        
        for line in process.stdout:
            log.info(line.rstrip('\n'))  
            output_lines.append(line.rstrip('\n'))
        for line in process.stderr:  
            log.error(line.rstrip('\n'))
            error_lines.append(line.rstrip('\n'))

        try:
            process.wait()
        except KeyboardInterrupt:
            process.send_signal(signal.SIGINT)
            process.wait()
            log.warning(f"Команда прервана пользователем: {command}")
            raise

        log.info(f"Команда завершена с кодом: {process.returncode}")

        output = '\n'.join(output_lines)
        errors = '\n'.join(error_lines)

        code = process.returncode == 0
        if returncode:
            if not errors:
                return output, code
            else:
                return errors, code
        else:
            if not errors:
                return output, True
            else:
                return errors, False
            

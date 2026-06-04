
import subprocess
import signal
import sys
import platform
import psutil

from pathlib import Path
from abc import ABC, abstractmethod
from typing import Union, Tuple
from functools import wraps
from os import linesep
from datetime import datetime


sys.path.insert(0, str(Path(__file__).parent))
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
        if returncode:
               return result.returncode
        else:
            if not errors:
                 return output
            else:
                 return errors
            

    @staticmethod
    def leave_command(command: str, returncode=None) -> Tuple[str, bool]:
        """
        Построчный вывод в терминал/лог
        """
        log.debug(f"Выполняется команда: {command}")

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

        log.debug(f"Команда '{command}' завершена с кодом: {process.returncode}")

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
            

    @staticmethod
    def get_system_info():
        return {
            'os_name': platform.system(),
            'os_version': platform.release(),
            'kernel_version': platform.version(),
            'cpu_model': platform.processor() or "Unknown",
            'cpu_cores': psutil.cpu_count(logical=False),
            'cpu_threads': psutil.cpu_count(logical=True),
            'ram_total': f"{psutil.virtual_memory().total / (1024**3):.1f} GB",
            'test_date': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'hostname': platform.node()
        }


class Writer:
    """
    Класс для записи результатов тестов в файл.
    """
    
    def __init__(self, 
                 file_name=None):
        """
        Инициализация Writer
        
        Args:
            file_name: путь к файлу для записи
        """
        self.fn = file_name
        
        if self.fn:
            Path(self.fn).parent.mkdir(parents=True, exist_ok=True)
    
    def wrs(self, cl=None, method=None, test=None, status=None, message=None):
        """
        Запись статуса выполнения теста
        
        Args:
            cl: класс (object или строка)
            method: метод (object или строка)
            test: имя теста (строка)
            status: статус выполнения (True/False или строка)
            message: дополнительное сообщение
        """

        cl_name = cl.__name__ if hasattr(cl, '__name__') else str(cl)
        method_name = method.__name__ if hasattr(method, '__name__') else str(method)

        status_str = "SUCCESS" if status else "FAILED"
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        record = {
            "timestamp": timestamp,
            "class": cl_name,
            "method": method_name,
            "test": test,
            "status": status_str,
            "code": status,
            "message": message or ""
        }
        
        self._write_txt(record)
    
    def _write_txt(self, record):
        """
        Запись в текстовом формате
        """
        with open(self.fn, 'a', encoding='utf-8') as f:
            f.write(f"{record['timestamp']} - {record['class']} - {record['method']}: {record['test']} - {record['status']}")
            if record['message']:
                f.write(f" - {record['message']}")
            f.write("\n")





import subprocess
import signal
import sys
import platform
import psutil
import threading

from time import sleep, time
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
    def leave_command(command: str, returncode=None, console=True, debug=False) -> Tuple[str, bool]:
        """
        Построчный вывод в терминал/лог
        """
        if debug:
            log.info(f"Выполняется команда: {command}")

        output_lines = []
        error_lines = []

        process = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, bufsize=1, universal_newlines=True)
        
        if not console:
            log.set_console(False)
            log.info(f"Выполняется команда: {command}")
            keepalive_active = False #True

            def keepalive():
                spinner = ['◐', '◓', '◑', '◒']
                idx = 0
                start = time()
                total_hours = 0
                total_minutes = 0
                total_seconds = 0
                while keepalive_active and process.poll() is None:
                    sleep(1)
                    if keepalive_active:
                        elapsed = int(time() - start)
                        total_hours = elapsed // 3600
                        total_minutes = (elapsed % 3600) // 60
                        total_seconds = elapsed % 60
                        sys.stdout.write(f'\r{spinner[idx]}  {total_hours:02d}:{total_minutes:02d}:{total_seconds:02d}')
                        sys.stdout.flush()
                        idx = (idx + 1) % len(spinner)
                sys.stdout.write(f'\rВыполнено за {total_hours:02d}:{total_minutes:02d}:{total_seconds:02d} \n')
                sys.stdout.flush()

            keepalive_thread = threading.Thread(target=keepalive, daemon=True)
            keepalive_thread.start()

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
        
        if not console:
            keepalive_active = False
            keepalive_thread.join(timeout=1)
            log.set_console(True)

        if process.returncode == 0:
            if debug:
                log.info(f"Команда '{command}' завершена с кодом: {process.returncode}\n")
        else: log.error(f"Команда '{command}' завершена с кодом: {process.returncode}\n")

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
        def _get_kernel():
            try:
                code = system.command("dpkg -s linux-image-`uname -r` | grep Version: | awk '{print $2}'", returncode=True)
                if code == 0:
                    kernel = system.command("dpkg -s linux-image-`uname -r` | grep Version: | awk '{print $2}'")
                    if kernel:
                        return kernel.strip()
            except:
                return None

        def _get_os_name():
            try:
                with open("/etc/astra/build_version", "r") as f:  
                    os_name = "Astra Linux" 
                    os_version = f"{f.read().strip()}"
                return os_name, os_version
            except:
                return None, None

        def _get_linux_cpu_model():
            try:
                with open('/proc/cpuinfo', 'r') as f:
                    for line in f:
                        if 'model name' in line:
                            cpu_model = line.split(':')[1].strip()
                            break
            except:
                cpu_model = "Unknown"
            return cpu_model
        
        cpu_model = platform.processor()
        if cpu_model == "Unknown" or not cpu_model:
            cpu_model = _get_linux_cpu_model()

        os_name, os_version = _get_os_name()
        if not os_name or not os_version:
            os_name = platform.system()
            os_version = platform.release()

        os_kernel = _get_kernel()
        if not os_kernel:
            os_kernel = platform.version()
        
        return {
            'os_name': os_name,
            'os_version': os_version,
            'kernel_version': os_kernel,
            'cpu_model': cpu_model,
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


class ProgressBar:
    """
    Прогресс-бар с отображением процентов и времени
    """
    
    # Веса этапов
    STAGE_WEIGHTS = {
        'unixbench': 61,      
        'fs_mark': 8,         
        'lmbench': 28,        
        'perf': 2,            
        'aggregation': 0.5,   
        'index': 0.5          
    }
    
    # Очередность этапов
    STAGE_ORDER = ['unixbench', 'fs_mark', 'lmbench', 'perf', 'aggregation', 'index']
    
    # Названия этапов для отображения
    STAGE_NAMES = {
        'unixbench': 'Ядро/системные вызовы',
        'fs_mark': 'Файловая система',
        'lmbench': 'Задержки',
        'perf': 'Синхронизация/события',
        'aggregation': 'Агрегация результатов',
        'index': 'Расчёт индекса'
    }
    
    def __init__(self):
        self.current_stage_index = 0
        self.stage_progress = 0  # 0-100 внутри текущего этапа
        self.start_time = datetime.now()
        self.running = True
        self.completed = False
        self.total_weight = sum(self.STAGE_WEIGHTS.values())
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._update, daemon=True)
        self.last_percent = 0
        
    def start(self):
        """Запускает поток обновления прогресс-бара"""
        self.thread.start()
        
    def stop(self):
        """Останавливает поток и завершает прогресс-бар"""
        self.completed = True
        self.running = False
        if self.thread.is_alive():
            self.thread.join(timeout=0.5)
        self._display(completed=True)
        
    def advance_stage(self):
        """Переход к следующему этапу"""
        self.stage_progress = 100
        self.current_stage_index += 1
        
    def update_progress(self, progress_percent):
        """Обновление прогресса внутри текущего этапа (0-100)"""
        self.stage_progress = min(100, progress_percent)
        
    def get_current_stage_name(self):
        """Получить название текущего этапа"""
        if self.current_stage_index < len(self.STAGE_ORDER):
            stage = self.STAGE_ORDER[self.current_stage_index]
            return self.STAGE_NAMES.get(stage, stage)
        return "Завершение"
        
    def get_current_weight(self):
        """Получить вес текущего этапа"""
        if self.current_stage_index < len(self.STAGE_ORDER):
            stage = self.STAGE_ORDER[self.current_stage_index]
            return self.STAGE_WEIGHTS.get(stage, 0)
        return 0
        
    def get_overall_progress(self):
        """Расчёт общего прогресса"""
        overall = 0
        for i, stage in enumerate(self.STAGE_ORDER):
            weight = self.STAGE_WEIGHTS.get(stage, 0)
            if i < self.current_stage_index:
                overall += weight
            elif i == self.current_stage_index:
                overall += weight * (self.stage_progress / 100)
        return min(99, overall)  # Не доходим до 100% до завершения
        
    def _update(self):
        """Фоновый поток обновления прогресс-бара"""
        while self.running:
            with self.lock:
                self._display()
            time.sleep(0.5)
            
    def _display(self, completed=False):
        """Отображает прогресс-бар"""
        # Вычисляем процент
        if completed:
            percent = 100
        else:
            percent = self.get_overall_progress()
        
        # Время выполнения
        elapsed = datetime.now() - self.start_time
        total_seconds = int(elapsed.total_seconds())
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        time_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        
        # Прогноз времени (ETA) на основе прогресса
        if percent > 0 and not completed:
            estimated_total = (total_seconds / percent) * 100
            remaining = estimated_total - total_seconds
            rem_hours = int(remaining // 3600)
            rem_minutes = int((remaining % 3600) // 60)
            rem_seconds = int(remaining % 60)
            eta_str = f"{rem_hours:02d}:{rem_minutes:02d}:{rem_seconds:02d}"
        else:
            eta_str = "00:00:00"
        
        # Создаём прогресс-бар
        bar_length = 40
        filled = int(bar_length * percent / 100)
        bar = '█' * filled + '░' * (bar_length - filled)
        
        # Статус и описание
        if completed:
            status = "ЗАВЕРШЕНО"
        elif percent >= 99:
            status = "ФИНИШИРУЕМ"
        else:
            status = "ВЫПОЛНЕНИЕ"
        
        # Название этапа
        stage_name = self.get_current_stage_name()
        
        # Выводим
        sys.stdout.write(f'\r{status} | {bar} | {percent:>5.1f}% | {stage_name:<35} | {time_str} | ETA: {eta_str}')
        sys.stdout.flush()
        
        if completed:
            sys.stdout.write('\n')
            sys.stdout.flush()

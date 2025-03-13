import subprocess
import os
import time

class system:
    """
    Класс для обращения к системе
    """
    @staticmethod
    def check_output_command(command: str) -> str:
        """
        Выполнение команды с проверкой вывода

        Args:
            command (str): команда которая должна быть выполнена

        Returns:
            str: Если не ошибок вывод от команды, если есть то ошибка
        """
        result = subprocess.Popen([command], shell=True, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, universal_newlines=True)
        output, errors = result.communicate()
        output = os.linesep.join([s for s in output.splitlines() if s])
        errors = os.linesep.join([s for s in errors.splitlines() if s])
        return output if not errors else errors

    @staticmethod
    def cmd_with_returncode(command: str) -> int:
        """
        Выполнение команды с возвращением кода завершения

        Args:
            command (str): команда которая должна быть выполнена

        Returns:
            int: код завершения
        """
        return subprocess.run(command, shell=True).returncode

    @staticmethod
    def cmd(command: str):
        """
        Выполнение команды без обработки

        Args:
            command (str): команда которая должна быть выполнена

        Returns:
            _type_: _description_
        """
        return subprocess.run(command, shell=True)
    
    def set_signal(set_signal: str):
        """
        Установка сигнала

        Args:
            set_signal (str): имя сигнала
        """
        signal_dir = './signal'
        signal_file_path = os.path.join(signal_dir, set_signal)
        
        os.makedirs(signal_dir, exist_ok=True)
        
        with open(signal_file_path, 'w') as file:
            file.write('1')

    def get_signal(get_signal: str):
        """
        Получение сигнала

        Args:
            get_signal (str): имя сигнала
        """
        signal_dir = './signal'
        signal_file_path = os.path.join(signal_dir, get_signal)
        timeout = 10 * 60  # 10 minutes
        interval = 5  # 5 seconds
        elapsed_time = 0

        while elapsed_time < timeout:
            if os.path.isfile(signal_file_path):
                with open(signal_file_path, 'r') as file:
                    content = file.read().strip()
                    if content == '1':
                        return True
            time.sleep(interval)
            elapsed_time += interval
        
        return False
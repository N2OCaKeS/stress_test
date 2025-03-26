import os
import time

class _Signals():
    """
    Класс для управления сигналами.

    Основные функции:
    - Установка сигнала (создание файла-сигнала).
    - Ожидание получения сигнала (проверка наличия файла-сигнала).
    - Удаление сигналов (удаление всех файлов-сигналов).
    
    Этот класс используется для синхронизации выполнения задач между различными процессами.
    """

    @staticmethod        
    def set(set_signal: str):
        """
        Устанавливает сигнал, создавая файл с указанным именем.

        Args:
            set_signal (str): Имя сигнала (файла), который будет создан.
        """
        signal_dir = './signal'
        signal_file_path = os.path.join(signal_dir, set_signal)
        
        if not os.path.exists(signal_dir):
            os.makedirs(signal_dir)
        
        with open(signal_file_path, 'w') as file:
            file.write('1')

    @staticmethod  
    def get(get_signal: str):
        """
        Ожидает получения сигнала, проверяя наличие файла с указанным именем.

        Args:
            get_signal (str): Имя сигнала (файла), который ожидается.

        Returns:
            bool: True, если сигнал получен (файл найден), иначе False.
        """
        signal_dir = './signal'
        signal_file_path = os.path.join(signal_dir, get_signal)
        timeout = 10 * 60  # 10 минут
        interval = 5       # 5 секунд
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

    @staticmethod
    def remove_all():
        """
        Удаляет все сигналы, то есть все файлы из директории './signal'.
        """
        signal_dir = './signal'
        if os.path.exists(signal_dir):
            for filename in os.listdir(signal_dir):
                file_path = os.path.join(signal_dir, filename)
                if os.path.isfile(file_path):
                    os.remove(file_path)

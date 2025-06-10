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
    def set(host: str, set_signal: str):
        """
        Устанавливает сигнал, добавляя запись host 1 в файл.

        Args:
            host (str): Имя хоста.
            set_signal (str): Имя сигнала (файла), который будет создан.
        """
        signal_dir = './signal'
        signal_file_path = os.path.join(signal_dir, set_signal)
        
        if not os.path.exists(signal_dir):
            os.makedirs(signal_dir)
        
        with open(signal_file_path, 'a') as file:
            file.write(f'{host} 1\n')

    @staticmethod  
    def get(get_signal: list, timeout_min: int = 15):
        """
        Ожидает получения сигнала, проверяя наличие файла с указанным именем и совпадение содержимого.

        Args:

            get_signal (list): Имя сигнала (файла), который ожидается.
                get_signal = ['hostname', 'signal_name']

        Returns:
            bool: True, если сигнал получен (файл найден и содержит нужную запись), иначе False.
        """
        signal_dir = './signal'
        signal_file_path = os.path.join(signal_dir, get_signal[1])
        timeout = timeout_min * 60  # 10 минут
        interval = 10      # 5 секунд
        elapsed_time = 0
        host = get_signal[0]


        while elapsed_time < timeout:
            if os.path.isfile(signal_file_path):
                with open(signal_file_path, 'r') as file:
                    lines = file.readlines()
                    for line in lines:
                        if line.strip() == f'{host} 1':
                            return True
            time.sleep(interval)
            elapsed_time += interval
        print('ОШИБКА Сигнал не найден')
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

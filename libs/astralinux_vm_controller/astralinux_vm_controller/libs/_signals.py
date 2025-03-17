import os
import time

class _signals():

    @staticmethod        
    def set(set_signal: str):
        """
        Установка сигнала

        Args:
            set_signal (str): имя сигнала
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
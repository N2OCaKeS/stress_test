import logging
import os

log_path = os.getenv('LOG_FILE_PATH', 'app.log')  # Берём путь из переменной окружения
open(log_path, 'w').close()

testrun_logger = logging.getLogger(__name__)
testrun_logger.setLevel(logging.INFO)

formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

file_handler = logging.FileHandler(log_path, mode="a")
file_handler.setFormatter(formatter)

console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)

testrun_logger.addHandler(file_handler)
testrun_logger.addHandler(console_handler)

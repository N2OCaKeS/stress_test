import logging
from conf import log_file


# Настройка логгера
logger = logging.getLogger('infocollector_logger')
logger.setLevel(logging.INFO)

file_handler = logging.FileHandler(log_file)
file_handler.setLevel(logging.DEBUG)

formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)


logger.addHandler(file_handler)


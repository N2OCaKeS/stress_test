#!/home/u/python/Python-3.12.1/venv/bin/python3.12
import sys
import logging
from logging.handlers import RotatingFileHandler
from bendiks_front import app
import datetime

app.secret_key = 'srv_2413'
log_file_dir = '/home/u/git/stress_test/bendiks_app/'
log_file_name = log_file_dir + 'error.log'
log_size = 52428800

logger = logging.getLogger(name=f'Bendiks Log {datetime.datetime.now()}\n')
logger.setLevel(level=logging.INFO)
handler = RotatingFileHandler(log_file_name, 
                              maxBytes=log_size, 
                              backupCount=10)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(funcName)s: %(lineno)d - %(message)s', 
                              datefmt='%Y/%m/%d %H:%M:%S')
handler.setFormatter(formatter)
logger.addHandler(handler)
sys.path.insert(0,"/home/u/git/stress_test/bendiks_app")


if __name__ == '__main__':
    app.run()

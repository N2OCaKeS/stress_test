#!/home/u/python/Python-3.12.1/venv/bin/python3.12
import sys
import logging
from logging.handlers import RotatingFileHandler
from allta_front import app
import datetime
import os
import re
from time import sleep
import threading

app.secret_key = 'srv_2413'
log_file_dir = '/home/u/git/stress_test/allta_app/'
log_file_name = 'error.log'
log_size = 52428800 

# def logrotate():
#     logger = logging.getLogger() 
#     handler = logger.handlers[0]
#     log_version = 1

#     while True:
#         files = os.listdir(log_file_dir)
#         logs_versions = [f for f in files if re.match(r'error(\.\d+)?\.log', f)]
#         if logs_versions:
#             versions = [int(re.search(r'\d+', f).group()) if re.search(r'\d+', f) else 0 for f in logs_versions]
#             log_version = max(versions) + 1

#         if os.path.isfile(log_file_dir + log_file_name):
#             if os.path.getsize(log_file_dir + log_file_name) >= log_size:
#                 if not os.path.isfile(f'{log_file_dir}error.{log_version}.log'):
#                     os.rename(log_file_dir + log_file_name, f'{log_file_dir}error.{log_version}.log')
#                     with open(log_file_dir + log_file_name, 'w') as wlog:
#                         wlog.write(f'Start new log {datetime.datetime.now()}')
#                     handler.close()
#                     new_handler = logging.FileHandler(log_file_dir + log_file_name)
#                     new_handler.setFormatter(handler.formatter) 
#                     logger.handlers = []
#                     logger.addHandler(new_handler)
#             else: sleep(3600)


logger_wsgi = logging.getLogger('logger_wsgi')
logger_wsgi.setLevel(logging.DEBUG)
handler_wsgi = RotatingFileHandler(log_file_dir + log_file_name, maxBytes=log_size, backupCount=5)
formatter_wsgi = logging.Formatter('%(asctime)s - %(levelname)s - %(funcName)s: %(lineno)d - %(message)s')
handler_wsgi.setFormatter(formatter_wsgi)
logger_wsgi.addHandler(handler_wsgi)

def handle_exception(exc_type, exc_value, exc_traceback):
    logger_wsgi.error("Uncaught exception",
        exc_info=(exc_type, exc_value, exc_traceback))

sys.excepthook = handle_exception
sys.path.insert(0,"/home/u/git/stress_test/allta_app")



#def run_app():
#    app.run(threaded=True)


#task1 = threading.Thread(target=logrotate, daemon=True)
#task2 = threading.Thread(target=run_app, daemon=True)    



if __name__ == '__main__':
    #task1.start()
    #task2.start()
    app.run()
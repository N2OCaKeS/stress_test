#!/home/u/python/Python-3.12.1/venv/bin/python3.12
import sys
import logging

logging.basicConfig(filename='/home/u/git/stress_test/bendiks_app/error.log',
                    format='%(asctime)s - %(levelname)s - %(funcName)s: %(lineno)d - %(message)s',
                    datefmt='%Y/%m/%d %H:%M:%S',
                    filemode='a')
sys.path.insert(0,"/home/u/git/stress_test/bendiks_app")

from bendiks_front import app
app.secret_key = 'srv_2413'

if __name__ == '__main__':
    app.run()

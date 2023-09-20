#!/usr/bin/python3
import sys
import logging


log_file = '/home/u/git/stress_test/bendiks_app/error.log'
sys.path.insert(0,'/home/u/git/stress_test/bendiks_app')


class MyHandler(logging.StreamHandler):

    def emit(self, record):
        self.stream.write('\n\n\n\n' + '='*50 + '\n')
        super().emit(record)


handler = MyHandler(open(log_file, 'a'))
handler.setFormatter(logging.Formatter('%(asctime)s %(message)s', datefmt='%m/%d/%Y %H:%M:%S'))


logger = logging.getLogger()
logger.setLevel(logging.DEBUG)
logger.addHandler(handler)

from bendiks_front import app
app.secret_key = 'srv_2113'

if __name__ == '__main__':
    app.run()



#!/usr/bin/python3
import sys
import logging

logging.basicConfig(filename='/home/u/git/stress_test/bendiks_app/error.log',
                    format='%(asctime)s %(message)s',
                    datefmt='%m/%d/%Y %I:%M:%S',
                    filemode='a')
sys.path.insert(0,"/home/u/git/stress_test/bendiks_app")

from bendiks_front import app
app.secret_key = 'srv_2113'

if __name__ == '__main__':
    app.run()

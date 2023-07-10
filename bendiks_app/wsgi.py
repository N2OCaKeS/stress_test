#!/usr/bin/python3
import sys
import logging

logging.basicConfig(stream=sys.stderr)
sys.path.insert(0,"/home/u/bendiks_app")
from bendiks_front import app as application
application.secret_key = 'srv_2113'

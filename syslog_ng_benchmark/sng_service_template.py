#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import socket
import logging
import logging.handlers
from syslog import syslog
import time

hostname = socket.gethostname()
ip = socket.gethostbyname(hostname)

while True:
    service_logger = logging.getLogger(__name__)
    service_logger.setLevel(logging.NOTSET)
    service_logger.addHandler(logging.handlers.SysLogHandler(address="/dev/log"))

    for port in range(1, 65535):
        service_logger.debug('Try to connect! warning message from {}'.format(__name__))
        service_logger.info('Try to connect! info message from {}'.format(__name__))
        service_logger.exception('Try to connect! exception message from {}'.format(__name__))
        service_logger.warning('Try to connect! warning message from {}'.format(__name__))
        service_logger.error('Try to connect! error message from {}'.format(__name__))
        service_logger.critical('Try to connect! critical message from {}'.format(__name__))

        try:
            network = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            socket.setdefaulttimeout(1)
            if network.connect_ex((ip, port)) == 0:  # если соединение успешное
                service_logger.debug('Connect! warning message from {}'.format(__name__))
                service_logger.info('Connect! info message from {}'.format(__name__))
                service_logger.exception('Connect! exception message from {}'.format(__name__))
                service_logger.warning('Connect! warning message from {}'.format(__name__))
                service_logger.error('Connect! error message from {}'.format(__name__))
                service_logger.critical('Connect! critical message from {}'.format(__name__))

            network.close()
            service_logger.debug('Disconnect! warning message from {}'.format(__name__))
            service_logger.info('Disconnect! info message from {}'.format(__name__))
            service_logger.exception('Disconnect! exception message from {}'.format(__name__))
            service_logger.warning('Disconnect! warning message from {}'.format(__name__))
            service_logger.error('Disconnect! error message from {}'.format(__name__))
            service_logger.critical('Disconnect! critical message from {}'.format(__name__))
        except:
            print("__Exit__")



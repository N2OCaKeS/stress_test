import os
from collections import deque

from logging_conf import main_logger


class UtilForBuildPath:
    @staticmethod
    def build_path(main_folder, stat_rc_vers=None):
        if stat_rc_vers:
            folder = "statistics_rc"
            if not stat_rc_vers in os.listdir(f"{main_folder}/{folder}"):
                os.mkdir(f"{main_folder}/{folder}/{stat_rc_vers}")
                main_logger.debug(f"{main_folder}/{folder}/{stat_rc_vers} отсутствует, поэтому создаем")
            folder = f"{folder}/{stat_rc_vers}"
        else:
            folder = "statistics"
        path = f"{main_folder}/{folder}"
        # if not folder in os.listdir(main_folder):
        #     os.mkdir(path)
        return path
    

class UtilForReadLogs:
    @staticmethod
    def read_last_lines(filename, num_lines=None, reverse=False):
        with open(filename, 'r') as file:
            lines = list(deque(file, num_lines)) if num_lines is not None else file.readlines()
            if reverse:
                lines = lines[::-1]
        return lines
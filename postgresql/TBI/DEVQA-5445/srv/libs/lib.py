import os
import subprocess

from os import linesep
from string import Template
from abc import ABC, abstractmethod
from typing import Union, Tuple



LIBS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPT_DIR = os.path.normpath(os.path.join(LIBS_DIR, '..', '..'))
CONFIG_FILE = 'psql_test.conf'
PARAMS = {
     'results_name': 'RESULTS_NAME',
     'psql_version': 'PSQL_VERSION',
     'cluster_port': 'CLUSTER_PORT',
     'connections_count': 'CONNECTIONS_COUNT',
     't_time': 'TRANSACTION_TIME',
     'shared_buffers': 'SHARED_BUFFERS',
     'eff_cache_size': 'EFFECTIVE_CACHE_SIZE',
     'work_mem': 'WORK_MEM',
     'max_worker_ps': 'MAX_WORKER_PROCESSES',
     'max_pl_workers': 'MAX_PARALLEL_WORKERS'
}



def conf_wrapper(file_name: str):
     with open(file_name, 'r') as r:
          config = r.readlines()

     dates = {
          line.split('=')[0]: line.split('=')[1].strip() for line in config if '=' in line
          }

     temp_dict = {
          key: dates.get(value) for key, value in PARAMS.items()
          }
     
     return temp_dict
     



class system:
    
    """
    Класс для обращения к системе
    """

    @staticmethod
    def check_output_command(command: str) -> Tuple[str, bool]:
        result = subprocess.Popen([command], shell=True, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, universal_newlines=True, text=True)
        output, errors = result.communicate()
        output = os.linesep.join([s for s in output.splitlines() if s])
        errors = os.linesep.join([s for s in errors.splitlines() if s])
        if not errors:
          return output, True
        else:
          return errors, False


    @staticmethod
    def cmd_with_returncode(command: str) -> int:
        return subprocess.run(command, shell=True).returncode

    @staticmethod
    def cmd(command: str):
        return subprocess.run(command, shell=True)



     
class Test(ABC):
    
    """
    Абстрактный конвейер
    """

    @abstractmethod
    def check_user(cls) -> Union[str, None]:
        """Проверка прав пользователя"""
        pass
    
    @abstractmethod
    def check_mode(cls) -> Union[str, None]:
        """Проверка режима ОС"""
        pass

    @abstractmethod
    def host_env_prepare(cls) -> Union[str, None]:
        """Подготовка окружения"""
        pass

    @abstractmethod
    def database_prep(cls) -> Union[str, None]:
        """Подготовка БД"""
        pass

    @abstractmethod
    def init_base(cls) -> Union[str, None]:
        """Инизиализация БД"""
        pass

    @abstractmethod
    def execute_test(cls) -> Union[str, None]:
        """Запуск теста"""
        pass
    
    @abstractmethod
    def cleare(cls) -> Union[str, None]:
        """Очистка окружения"""
        pass

     


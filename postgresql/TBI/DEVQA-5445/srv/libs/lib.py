import os
import subprocess

from os import linesep
from string import Template
from abc import ABC, abstractmethod



LIBS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPT_DIR = os.path.normpath(os.path.join(LIBS_DIR, '..', '..'))
CONFIG_FILE = 'psql_test.conf'
PARAMS = {
     'project_path': 'PROJECT_PATH',
     'results_path': 'RESULTS_PATH',
     'psql_version': 'PSQL_VERSION',
     'max_connections': 'MAX_CONNECTIONS'
}



def conf_wrapper(file_name: str):
     with open(file_name, 'r') as r:
          config = r.readlines()

     dates = {
          line.split('=')[0]: line.split('=')[1].strip() for line in config if '=' in line
          }
     #print(dates)

     temp_dict = {
          key: dates.get(value) for key, value in PARAMS.items()
          }
     #print(temp_dict)
     return temp_dict
     



class system:
    """
    Класс для обращения к системе
    """
    @staticmethod
    def check_output_command(command: str) -> str:
        result = subprocess.Popen([command], shell=True, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, universal_newlines=True)
        output, errors = result.communicate()
        output = os.linesep.join([s for s in output.splitlines() if s])
        errors = os.linesep.join([s for s in errors.splitlines() if s])
        return output if not errors else errors, 1

    @staticmethod
    def cmd_with_returncode(command: str) -> int:
        return subprocess.run(command, shell=True).returncode

    @staticmethod
    def cmd(command: str):
        return subprocess.run(command, shell=True)



     
class Test(ABC):
    """
    Абстрактный конвейер\n
    check_user: Проверка прав пользователя\n
    check_mode: Проверка режима ОС\n
    host_env_prepare: Подготовка окружения\n
    database_prep: Подготовка БД\n
    init_base: Инизиализация БД\n
    execute_test: Запуск теста\n
    cleare: Очистка окружения
    """
    @abstractmethod
    def check_user(cls) -> str | any:
        pass
    
    @abstractmethod
    def check_mode(cls) -> str | any:
        pass

    @abstractmethod
    def host_env_prepare(cls) -> str | any:
        pass

    @abstractmethod
    def database_prep(cls) -> str | any:
        pass

    @abstractmethod
    def init_base(cls) -> str | any:
        pass

    @abstractmethod
    def execute_test(cls) -> str | any:
        pass
    
    @abstractmethod
    def cleare(cls) -> str | any:
        pass

     


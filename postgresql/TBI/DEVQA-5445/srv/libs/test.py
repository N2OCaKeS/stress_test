import logging

from functools import wraps
from sys import exit
from os import linesep

from srv.libs.lib import (
     SCRIPT_DIR,
     CONFIG_FILE,
     LIBS_DIR,
     conf_wrapper,
     system,
     Test
)



class PSQLLoadTest(Test):

    """
    Класс подготовки окружения для нагрузочного тестирования PostgreSQL,
    запуска теста и последующей очистки окружения.

    Attributes:
        checking_user (bool): Признак проверки прав пользователя.
        checking_mode (bool): Признак проверки режима ОС.
        host_prepare (bool): Признак подготовки хостового окружения.
        db_prep (bool): Признак подготовки базы данных.
        init_db (bool): Признак инициализации базы данных.
        execute (bool): Признак выполнения теста.
        clear_env (bool): Признак очистки окружения.
    """

    def __init__(self,
                 checking_user=True,
                 checking_mode=True,
                 host_prepare=True,
                 db_prep=True,
                 init_bd=True,
                 execute=True,
                 cleare_env=False):
        
        self.checking_user = checking_user
        self.checking_mode = checking_mode
        self.h_prepare = host_prepare
        self.db_prep = db_prep
        self.init_bd = init_bd
        self.execute = execute
        self.cleare_env = cleare_env
        self.config = conf_wrapper(f'{SCRIPT_DIR}/{CONFIG_FILE}')

        logging.basicConfig(
            filename=f"{SCRIPT_DIR}/psql_test.log", 
            level=logging.INFO, 
            filemode='a',
            format='%(asctime)s - %(levelname)s - %(funcName)s: %(lineno)d - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
        )
        logging.info('\n\n\nStart logging\n')


    @staticmethod
    def status_checker(method):

        """
        Декоратор, который выводит сообщение с статусом, 
        сигнализирующем об успешности выполнения метода
        """

        @wraps(method)
        def wrapper(self, *args, **kwargs):
            print(f"Метод '{method.__name__}' вызван")
            logging.info(f"Метод '{method.__name__}' вызван")
            try:
                result, status  = method(self, *args, **kwargs)
                if not status:
                    print(f'При выполнении метода "{method.__name__}" произошла ошибка')
                    logging.error(f'При выполнении метода "{method.__name__}" произошла ошибка')
                    logging.error(result)
                    logging.info('End logging\n')
                    exit(1)
                else: 
                    logging.info(result)
                    print(f'Метод "{method.__name__}" успешно выполнен')
                    logging.info(f'Метод "{method.__name__}" успешно выполнен')
                return result
            except Exception as e:
                print(f'Method: {method.__name__}\nError is: {str(type(e).__name__)}\nMessage: {str(e)}')
                logging.error(f'Method: {method.__name__}\nError is: {str(type(e).__name__)}\nMessage: {str(e)}')
                logging.info('End logging\n')
                exit(1)
        return wrapper


    @status_checker
    def check_user(self):
        if self.checking_user:
            return system.check_output_command('sudo -n true')
        
    @status_checker
    def check_mode(self):
        if self.checking_mode:
            cmds = {
                'mode': 'sudo astra-modeswitch get',
                'mac': 'sudo astra-mac-control status',
                'mic': 'sudo astra-mic-control status'
            }
            temp_dict = {
                key: system.check_output_command(value) for key, value in cmds.items()
            }

            if temp_dict['mode'] != '2' or temp_dict['mac'] != 'АКТИВНО' or temp_dict['mic'] != 'АКТИВНО':
                return temp_dict, False
            else: return temp_dict, True

    @status_checker
    def host_env_prepare(self):
        if self.h_prepare:
            return system.check_output_command(f"sudo bash {LIBS_DIR}/h_prepare.sh {SCRIPT_DIR}")
                
    @status_checker
    def database_prep(self):
        if self.db_prep:
            return system.check_output_command(f"sudo bash {LIBS_DIR}/db_prep.sh {self.config['psql_version']} {SCRIPT_DIR} \
                                               {self.config['cluster_port']} {self.config['shared_buffers']} \
                                                {self.config['eff_cache_size']} {self.config['work_mem']} \
                                                    {self.config['max_worker_ps']} {self.config['max_pl_workers']}")

    @status_checker
    def init_base(self):
        if self.init_bd:
            return system.check_output_command(f"pgbench -i -h localhost --macs -p {self.config['cluster_port']} -U postgres -s 500 -F 100 test_parsec")
    
    @status_checker
    def execute_test(self):
        if self.execute:
            cmd = f"pgbench -h localhost --macs -p {self.config['cluster_port']} -U u_1 --random-seed=13 -T {self.config['t_time']} \
                  -j {self.config['connections_count']} -c {self.config['connections_count']} test_parsec"
            results, status = system.check_output_command(cmd)

            with open(f"{SCRIPT_DIR}/{self.config['results_name']}", 'w') as w:
                w.write(results)
            
            print(results)
            logging.info('\n\nEnd logging\n')

            return results, status

    def cleare(self):
        if self.cleare_env:
            try:
                system.cmd('sudo userdel u_1 -y')
                system.cmd(f"sudo apt-get purge -y postgresql-{self.config['psql_version']}")
                system.cmd(f"sudo rm -r /var/lib/postgresql/{self.config['psql_version']}")
                system.cmd(f"sudo rm -rf {SCRIPT_DIR}/{self.config['results_name']}")
                system.cmd(f"sudo rm -rf {SCRIPT_DIR}/psql_test.log")
            except Exception as e:
                print(f'Error is: {str(type(e).__name__)}\nMessage: {str(e)}')




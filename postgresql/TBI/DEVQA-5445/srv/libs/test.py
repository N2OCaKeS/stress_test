import logging

from functools import wraps
from sys import exit

from lib import (
     SCRIPT_DIR,
     CONFIG_FILE,
     LIBS_DIR,
     conf_wrapper,
     system,
     Test
)



class PSQLLoadTest(Test):
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
            filename=f'{self.config['project_path']}/psql_test.log', 
            level=logging.INFO, 
            filemode='a',
            format='%(asctime)s - %(levelname)s - %(funcName)s: %(lineno)d - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
        )
        logging.info('\n\n\nStart logging\n')


    @staticmethod
    def status_checker(method):
        '''
        Декоратор, который выводит сообщение с статусом, 
        сигнализирующем об успешности выполнения метода
        '''
        @wraps(method)
        def wrapper(self, *args, **kwargs):
            print(f"Метод '{method.__name__}' вызван")
            logging.info(f"Метод '{method.__name__}' вызван")
            try:
                result, status = method(self, *args, **kwargs)
                if status and status != 0:
                    print(f'При выполнении метода "{method.__name__}" произошла ошибка')
                    logging.error(f'При выполнении метода "{method.__name__}" произошла ошибка')
                    logging.error(result)
                    exit(1)
                else: 
                    logging.info(result)
                    print(f'Метод "{method.__name__}" успешно выполнен')
                    logging.info(f'Метод "{method.__name__}" успешно выполнен')
                return result
            except Exception as e:
                print(f'Method: {method.__name__}\nError is: {str(type(e).__name__)}\nMessage: {str(e)}')
                logging.error(f'Method: {method.__name__}\nError is: {str(type(e).__name__)}\nMessage: {str(e)}')
        return wrapper


    @status_checker
    def check_user(self):
        if self.checking_user:
            return system.check_output_command(f'sudo -n true')
        
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
                return temp_dict, 1

    @status_checker
    def host_env_prepare(self):
        if self.h_prepare:
            return system.check_output_command(f'sudo bash {LIBS_DIR}/h_prepare.sh')
                
    @status_checker
    def database_prep(self):
        if self.db_prep:
            return system.check_output_command(f'sudo bash {LIBS_DIR}/db_prep.sh {self.config['psql_version']} {SCRIPT_DIR}')

    @status_checker
    def init_base(self):
        if self.init_bd:
            return system.check_output_command('pgbench -i -h localhost --macs -p 6000 -U postgres -s 500 -F 100 test_parsec')
    
    @status_checker
    def execute_test(self):
        if self.execute:
            cmd = f'pgbench -h localhost --macs -p 6000 -U u_1 --random-seed=13 -T 30 -j {self.config['max_connections']}\
                  -c {self.config['max_connections']} test_parsec'
            results = system.check_output_command(cmd)

            with open(self.config['results_path'], 'w') as w:
                w.write(results)
            
            print(results)
            logging.info('\n\n\End logging\n')

            return results

    @status_checker
    def cleare(self):
        if self.cleare_env:



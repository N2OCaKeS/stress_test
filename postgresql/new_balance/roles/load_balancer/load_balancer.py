from allta import VBoxManager
from roles.vm_info import VMS_GROUPS, VMS_DATES, POSTGRES_DATA_PATH, POSTGRES_PORT

class LoadBalancer():
    def __init__(self):
        self.provider = VBoxManager()

    def load(self):

        provider = self.provider

        pgpool_config_path = '/etc/pgpool2'

        sed = {
            'g_load_balancer': [
                {
                    # pool_hba.conf
                    'path': f'{pgpool_config_path}/pool_hba.conf',
                    'old': 'host    all         all         127.0.0.1/32          trust',
                    'new': 'host    all         all         0.0.0.0/0          trust'
                },
                {   # pgpool.conf
                    'path': f'{pgpool_config_path}/pgpool.conf',
                    'old': '#listen_addresses = \'localhost\'',
                    'new': 'listen_addresses = \'*\''
                },
                {
                    'path': f'{pgpool_config_path}/pgpool.conf',
                    'old': '#port = 5433',
                    'new': 'port = 5440'
                },
                {
                    'path': f'{pgpool_config_path}/pgpool.conf',
                    'old': '#pcp_listen_addresses = \'localhost\'',
                    'new': 'pcp_listen_addresses = \'*\''
                },                
                {
                    'path': f'{pgpool_config_path}/pgpool.conf',
                    'old': '#enable_pool_hba = off',
                    'new': 'enable_pool_hba = on'
                },
                {
                    'path': f'{pgpool_config_path}/pgpool.conf',
                    'old': '#load_balance_mode = on',
                    'new': 'load_balance_mode = on'
                },
                {
                    'path': f'{pgpool_config_path}/pgpool.conf',
                    'old': '#health_check_period = 0',
                    'new': 'health_check_period = 3'
                },
                {
                    'path': f'{pgpool_config_path}/pgpool.conf',
                    'old': '#health_check_timeout = 20',
                    'new': 'health_check_timeout = 10'
                },
                {
                    'path': f'{pgpool_config_path}/pgpool.conf',
                    'old': '#failover_command = ''',
                    'new': 'failover_command = \'/tmp/pgpool.sh failover\''  # TODO Проверить скрипт failover
                },
                {
                    'path': f'{pgpool_config_path}/pgpool.conf',
                    'old': '#failback_command = ''',
                    'new': 'failback_command = \'/tmp/pgpool.sh failback\''  # TODO Проверить скрипт failback
                },
                {
                    'path': f'{pgpool_config_path}/pgpool.conf',
                    'old': '#auto_failback = off',
                    'new': 'auto_failback = on'
                },
                {
                    'path': f'{pgpool_config_path}/pgpool.conf',
                    'old': '#delegate_IP = \'\'',
                    'new': 'delegate_IP = \'10.177.103.131\''
                },
                {
                    'path': f'{pgpool_config_path}/pgpool.conf',
                    'old': '#if_cmd_path = \'/sbin\'',
                    'new': 'if_cmd_path = \'/sbin\''
                },
                {
                    'path': f'{pgpool_config_path}/pgpool.conf',
                    'old': '#if_up_cmd = \'/usr/bin/sudo /sbin/ip addr add $_IP_$/24 dev eth0 label eth0:0\'',
                    'new': 'if_up_cmd = \'/usr/bin/sudo /sbin/ip addr add $_IP_$/24 dev eth0 label eth0:0\'' # TODO проверить команду
                },
                {
                    'path': f'{pgpool_config_path}/pgpool.conf',
                    'old': '#if_down_cmd = \'/usr/bin/sudo /sbin/ip addr del $_IP_$/24 dev eth0\'',
                    'new': 'if_down_cmd = \'/usr/bin/sudo /sbin/ip addr del $_IP_$/24 dev eth0\'' # TODO проверить команду
                },
                {
                    'path': f'{pgpool_config_path}/pgpool.conf',
                    'old': '#arping_cmd = \'/usr/bin/sudo /usr/sbin/arping -U $_IP_$ -w 1 -I eth0\'',
                    'new': 'arping_cmd = \'/usr/bin/sudo /usr/sbin/arping -U $_IP_$ -w 1 -I eth0\'' # TODO проверить команду
                },
                # {
                #     'path': f'{pgpool_config_path}/pgpool.conf',
                #     'old': '',
                #     'new': ''
                # },                                               
            ]
        }

        provider.sed(sed, VMS_DATES, VMS_GROUPS)

        scp = {
                'mode': 'push',
                'path_host': 'postgresql/new_balance/roles/load_balancer/template/pgpool.sh', 
                'path_vm': '/tmp/contrprimer.sql'
        }
        provider.scp(scp, VMS_DATES, VMS_GROUPS)

        new_block = f"""backend_hostname0 = '{VMS_DATES['database1']['ip_bridge']}'
backend_port0 = {POSTGRES_PORT}
backend_weight0 = 1
backend_data_directory0 = '{POSTGRES_DATA_PATH}'
backend_flag0 = 'ALLOW_TO_FAILOVER'
backend_application_name0 = 'database1'

backend_hostname1 = '{VMS_DATES['database1']['ip_bridge']}'
backend_port1 = {POSTGRES_PORT}
backend_weight1 = 1
backend_data_directory1 = '{POSTGRES_DATA_PATH}'
backend_flag1 = 'ALLOW_TO_FAILOVER'
backend_application_name1 = 'database2'

backend_hostname1 = '{VMS_DATES['database1']['ip_bridge']}'
backend_port1 = {POSTGRES_PORT}
backend_weight1 = 1
backend_data_directory1 = '{POSTGRES_DATA_PATH}'
backend_flag1 = 'ALLOW_TO_FAILOVER'
backend_application_name1 = 'database3'
"""

        start_pgpool = {
            'g_load_balancer':{
                'set postgres privilege': {
                    'command': f'sudo pdpl-user -l 0:3 -i 63 -c 0:8 postgres && \
                        sudo usermod -a -G shadow postgres && \
                        sudo setfacl -d -m u:postgres:r /etc/parsec/macdb && \
                        sudo setfacl -R -m u:postgres:r /etc/parsec/macdb && \
                        sudo setfacl -m u:postgres:rx /etc/parsec/macdb && \
                        sudo setfacl -d -m u:postgres:r /etc/parsec/capdb && \
                        sudo setfacl -R -m u:postgres:r /etc/parsec/capdb && \
                        sudo setfacl -m u:postgres:rx /etc/parsec/capdb',
                    'signal set': '',
                    'signal get': ''
                },
                'backend hosts':{
                    'command':f'echo {new_block} | sudo tee -a {pgpool_config_path}/pgpool.conf',
                    'signal set':'set backend host',
                    'signal get':['']
                },
                'enable pgpool':{
                    'command':'sudo systemctl enable pgpool.service',
                    'signal set':'',
                    'signal get':['']
                },
                'start pgpool':{
                    'command':'sudo systemctl start pgpool.service',
                    'signal set':'',
                    'signal get':['set backend host']
                },
                # 'enable pgpool':{
                #     'command':'',
                #     'signal set':'',
                #     'signal get':['']
                # },                
            }
        }

        provider.execute(VMS_DATES, start_pgpool, VMS_GROUPS)


        

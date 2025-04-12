from allta import VBoxManager
from roles.vm_info import VMS_GROUPS, VMS_DATES, POSTGRES_DATA_PATH, POSTGRES_PORT, USERNAME, PASSWORD
from roles.load_balancer.keepalived import keepalived

class LoadBalancer():
    def __init__(self):
        self.provider = VBoxManager()

    def load(self):
        
        keepalived.keepalived()
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
                {   
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
                    'new': 'health_check_timeout = 5'
                },
                {
                    'path': f'{pgpool_config_path}/pgpool.conf',
                    'old': '#health_check_user = \'nobody\'',
                    'new': 'health_check_user = \'postgres\''
                },
                    {
                    'path': f'{pgpool_config_path}/pgpool.conf',
                    'old': '#health_check_max_retries = 0',
                    'new': 'health_check_max_retries = 2'
                }, 
                    {
                    'path': f'{pgpool_config_path}/pgpool.conf',
                    'old': '#health_check_retry_delay = 1',
                    'new': 'health_check_retry_delay = 1'
                },                                  
                {
                    'path': f'{pgpool_config_path}/pgpool.conf',
                    'old': '#failover_command = \'\'',
                    'new': 'failover_command = \'/tmp/pgpool.sh %d %h %p %D %m %H %P %r %R\''  # TODO Проверить скрипт failover
                },
                {
                    'path': f'{pgpool_config_path}/pgpool.conf',
                    'old': '#failback_command = \'\'',
                    'new': 'failback_command = \'/tmp/pgpool.sh %d %h %p %D %m %H %P %r %R\''  # TODO Проверить скрипт failback
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
                # {
                #     'path': f'{pgpool_config_path}/pgpool.conf', # TODO Нет такого пакета в main repo перепроверить нужен ли он
                #     'old': '#arping_cmd = \'/usr/bin/sudo /usr/sbin/arping -U $_IP_$ -w 1 -I eth0\'',
                #     'new': 'arping_cmd = \'/usr/bin/sudo /usr/sbin/arping -U $_IP_$ -w 1 -I eth0\'' 
                # },
                # {
                #     'path': f'{pgpool_config_path}/pgpool.conf',
                #     'old': '',
                #     'new': ''
                # },                                               
            ]
        }

        provider.sed(sed_conf=sed, vms_dates=VMS_DATES, vms_groups=VMS_GROUPS, username=USERNAME, password=PASSWORD)

        scp = {
            'g_load_balancer':{
                'mode': 'push',
                'path_host': './roles/load_balancer/template/pgpool.sh', 
                'path_vm': '/tmp/contrprimer.sql'
            }
        }
        provider.scp(scp_settings=scp, vms_dates=VMS_DATES, vms_groups=VMS_GROUPS, username=USERNAME, password=PASSWORD)

        new_block = f"""backend_hostname0 = '{VMS_DATES['database1']['ip_bridge']}'
backend_port0 = {POSTGRES_PORT}
backend_weight0 = 1
backend_data_directory0 = '{POSTGRES_DATA_PATH}'
backend_flag0 = 'ALLOW_TO_FAILOVER'
backend_application_name0 = 'database1'
backend_hostname1 = '{VMS_DATES['database2']['ip_bridge']}'
backend_port1 = {POSTGRES_PORT}
backend_weight1 = 1
backend_data_directory1 = '{POSTGRES_DATA_PATH}'
backend_flag1 = 'ALLOW_TO_FAILOVER'
backend_application_name1 = 'database2'
backend_hostname2 = '{VMS_DATES['database3']['ip_bridge']}'
backend_port2 = {POSTGRES_PORT}
backend_weight2 = 1
backend_data_directory2 = '{POSTGRES_DATA_PATH}'
backend_flag2 = 'ALLOW_TO_FAILOVER'
backend_application_name2 = 'database3'
EOF
"""


        start_pgpool = {
            'g_load_balancer':{
                'set chmod failoverscripts':{
                    'command':'sudo chmod +x /tmp/pgpool.sh && sudo mkdir -p /var/log/pgpool && sudo touch /var/log/pgpool/cluster_failover.log && sudo chown -R postgres:postgres /var/log/pgpool',
                    'signal set':'',
                    'signal get':''
                },                  
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
                    'command':f'sudo tee -a {pgpool_config_path}/pgpool.conf <<EOF\n{new_block}',
                    'signal set':'set backend host',
                    'signal get': ''
                },

                'fix pgpool2.service':{
                    'command':"sudo sed -i '/^\\[Service\\]/a CapabilitiesParsec=PARSEC_CAP_PRIV_SOCK PARSEC_CAP_MAC_SOCK' /lib/systemd/system/pgpool2.service",
                    'signal set':'Update pgpool service',
                    'signal get': ''
                },
                'reload daemon':{
                    'command':'sudo systemctl daemon-reload',
                    'signal set':'daemon reload',
                    'signal get':['Update pgpool service']
                },                 

                'enable pgpool':{
                    'command':'sudo systemctl enable pgpool2.service',
                    'signal set':'',
                    'signal get':['daemon reload']
                },
                'start pgpool':{
                    'command':'sudo systemctl start pgpool2.service',
                    'signal set':'',
                    'signal get':['daemon reload']
                },              
            }
        }

        provider.execute(commands=start_pgpool, vms_dates=VMS_DATES, vms_groups=VMS_GROUPS, username=USERNAME, password=PASSWORD)


        

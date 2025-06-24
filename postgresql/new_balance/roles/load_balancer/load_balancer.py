from new_balance.roles.load_balancer.keepalived import keepalived
from new_balance.roles.vm_info import (PASSWORD, PGPOOL_HOSTNAME, PGPOOL_PASSWORD,
                           PGPOOL_PASSWORD_MD5, PGPOOL_PCP_USER,
                           POSTGRES_DATA_PATH, POSTGRES_PORT, USERNAME,
                           VERSION_OS, VMS_DATES, VMS_GROUPS, PROVIDER)


class LoadBalancer():
    def __init__(self):
        self.provider = PROVIDER

    def load(self):

        keepalived.keepalived()
        provider = self.provider

        pgpool_config_path = '/etc/pgpool2'

        sed = {
            'g_load_balancer': [
                # Базовые параметры подключения
                {
                    'path': f'{pgpool_config_path}/pgpool.conf',
                    'old': '#listen_addresses = \'localhost\'',
                    'new': 'listen_addresses = \'*\''
                },
                {
                    'path': f'{pgpool_config_path}/pgpool.conf',
                    'old': '#port = 9999',
                    'new': 'port = 5440'
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


                # Настройки проверки состояния
                {
                    'path': f'{pgpool_config_path}/pgpool.conf',
                    'old': '#health_check_period = 0',
                    'new': 'health_check_period = 5'
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
                    'new': 'health_check_max_retries = 5'
                },
                {
                    'path': f'{pgpool_config_path}/pgpool.conf',
                    'old': '#health_check_retry_delay = 1',
                    'new': 'health_check_retry_delay = 3'
                },

                # Настройки репликации
                {
                    'path': f'{pgpool_config_path}/pgpool.conf',
                    'old': '#sr_check_period = 10',
                    'new': 'sr_check_period = 2'
                },
                {
                    'path': f'{pgpool_config_path}/pgpool.conf',
                    'old': '#sr_check_user = \'nobody\'',
                    'new': 'sr_check_user = \'postgres\''
                },
                {
                    'path': f'{pgpool_config_path}/pgpool.conf',
                    'old': '#replicate_select = off',
                    'new': 'replicate_select = on'
                },

                # Настройки failover/failback/recovery
                {
                    'path': f'{pgpool_config_path}/pgpool.conf',
                    'old': '#failover_command = \'\'',
                    'new': f'failover_command = \'/tmp/failover.sh %d %h %p {POSTGRES_DATA_PATH} %m %H %M %P %r {POSTGRES_DATA_PATH} %H %P {VMS_DATES['database1']['ip_bridge']} {VMS_DATES['database2']['ip_bridge']} {VMS_DATES['database3']['ip_bridge']}\''
                },
                {
                    'path': f'{pgpool_config_path}/pgpool.conf',
                    'old': '#failover_on_backend_error = on',
                    'new': 'failover_on_backend_error = on'
                },
                {
                    'path': f'{pgpool_config_path}/pgpool.conf',
                    'old': '#auto_failback = off',
                    'new': 'auto_failback = on'
                },

                # Настройки аутентификации
                {
                    'path': f'{pgpool_config_path}/pgpool.conf',
                    'old': '#enable_pool_hba = off',
                    'new': 'enable_pool_hba = on'
                },

                # Настройки балансировки
                {
                    'path': f'{pgpool_config_path}/pgpool.conf',
                    'old': '#load_balance_mode = on',
                    'new': 'load_balance_mode = on'
                },
                {
                    'path': f'{pgpool_config_path}/pgpool.conf',
                    'old': '#disable_load_balance_on_write = \'transaction\'',
                    'new': 'disable_load_balance_on_write = \'transaction\''
                },

                # enable log
                {
                    'path': f'{pgpool_config_path}/pgpool.conf',
                    'old': "#log_destination = 'stderr'",
                    'new': "log_destination = 'stderr'"
                },    
                {
                    'path': f'{pgpool_config_path}/pgpool.conf',
                    'old': "#log_connections = off",
                    'new': "log_connections = on"
                },
                {
                    'path': f'{pgpool_config_path}/pgpool.conf',
                    'old': "#log_disconnections = off",
                    'new': "log_disconnections = on"
                },
                {
                    'path': f'{pgpool_config_path}/pgpool.conf',
                    'old': "#log_hostname = off",
                    'new': "log_hostname = on"
                },
                {
                    'path': f'{pgpool_config_path}/pgpool.conf',
                    'old': "#log_statement = off",
                    'new': "log_statement = on"
                },
                {
                    'path': f'{pgpool_config_path}/pgpool.conf',
                    'old': "#log_min_messages = warning",
                    'new': "log_min_messages = debug5"
                },
                {
                    'path': f'{pgpool_config_path}/pgpool.conf',
                    'old': "#client_min_messages = notice",
                    'new': "client_min_messages = debug5"
                },        
                {
                    'path': f'{pgpool_config_path}/pgpool.conf',
                    'old': "#log_rotation_size = 10MB",
                    'new': "log_rotation_size = 1024MB"
                },                                                                                                                     
            ]
        }

        provider.sed(sed_conf=sed, vms_dates=VMS_DATES,
                     vms_groups=VMS_GROUPS, username=USERNAME, password=PASSWORD)

        scp = {
            'g_load_balancer': [
                {
                    'mode': 'push',
                    'path_host': f'./new_balance/roles/load_balancer/template/fix_failover.sh',
                    'path_vm': '/tmp/failover.sh'
                 },
            ]
        }

        provider.scp(scp_settings=scp, vms_dates=VMS_DATES,
                     vms_groups=VMS_GROUPS, username=USERNAME, password=PASSWORD)
        # scp = {
        #     'g_load_balancer': {
        #         'mode': 'push',
        #         'path_host': f'./roles/load_balancer/template/follow.sh',
        #         'path_vm': '/tmp/follow.sh'
        #     },
        # }
        # provider.scp(scp_settings=scp, vms_dates=VMS_DATES,
        #              vms_groups=VMS_GROUPS, username=USERNAME, password=PASSWORD)
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
            'g_load_balancer': {
                'set chmod failover scripts': {
                    'command': 'sudo chmod +x /tmp/*.sh  && sudo mkdir -p /var/log && sudo touch /var/log/pgpool_failover.log && sudo chown -R postgres:postgres /var/log/pgpool_failover.log',
                    'signal set': '',
                    'signal get': ''
                },
                'configure pcp': {
                    'command': f"echo '{PGPOOL_PCP_USER}:{PGPOOL_PASSWORD_MD5}' | sudo tee -a /etc/pgpool2/pcp.conf && sudo bash -c \"printf '{PGPOOL_HOSTNAME}:9898:{PGPOOL_PCP_USER}:{PGPOOL_PASSWORD}\\n' > /tmp/.pcppass\" && sudo chmod 600 /tmp/.pcppass",
                    'signal set': '',
                    'signal get': ''
                },
                'config pool_hba': {
                    'command': "echo -e 'host all all 127.0.0.1/32 trust\nhost all all 0.0.0.0/0 trust' | sudo tee -a /etc/pgpool2/pool_hba.conf",
                    'signal set': '',
                    'signal get': ''
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
                'backend hosts': {
                    'command': f'sudo tee -a {pgpool_config_path}/pgpool.conf <<EOF\n{new_block}',
                    'signal set': 'set backend host',
                    'signal get': ''
                },

                'fix pgpool2.service': {
                    'command': "sudo sed -i '/^\\[Service\\]/a CapabilitiesParsec=PARSEC_CAP_PRIV_SOCK PARSEC_CAP_MAC_SOCK' /lib/systemd/system/pgpool2.service",
                    'signal set': 'Update pgpool service',
                    'signal get': ''
                },
                'reload daemon': {
                    'command': 'sudo systemctl daemon-reload',
                    'signal set': 'daemon reload',
                    'signal get': ['Update pgpool service']
                },

                'enable pgpool': {
                    'command': 'sudo systemctl enable pgpool2.service',
                    'signal set': '',
                    'signal get': ['daemon reload']
                },
                'start pgpool': {
                    'command': 'sudo systemctl start pgpool2.service',
                    'signal set': '',
                    'signal get': ['daemon reload']
                },
            }
        }

        provider.execute(commands=start_pgpool, vms_dates=VMS_DATES,
                         vms_groups=VMS_GROUPS, username=USERNAME, password=PASSWORD)

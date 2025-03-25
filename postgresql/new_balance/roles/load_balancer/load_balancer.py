class load_balancer():
    def load_balancer():
        pgpool_path = '/etc/pgpool2'
        common_settings = {
            'g_load_balaner': {
                'path': f'{pgpool_path}/pgpool.conf',
                'old': '#listen_addresses = \'localhost\'',
                'new': 'listen_addresses = \'*\''
            },
            'g_load_balaner': {
                'path': f'{pgpool_path}/pgpool.conf',
                'old': '#port = 9999',
                'new': 'port = 9999'
            },
            'g_load_balaner': {
                'path': f'{pgpool_path}/pgpool.conf',
                'old': '#pcp_port = 9898',
                'new': 'pcp_port = 9898'
            },
            'g_load_balaner': {
                'path': f'{pgpool_path}/pgpool.conf',
                'old': '#enable_pool_hba = off',
                'new': 'enable_pool_hba = on'
            },
            'g_load_balaner': {
                'path': f'{pgpool_path}/pgpool.conf',
                'old': '#master_slave_mode = off',
                'new': 'master_slave_mode = on'
            },
            'g_load_balaner': {
                'path': f'{pgpool_path}/pgpool.conf',
                'old': '#master_slave_sub_mode = \'slony\'',
                'new': 'master_slave_sub_mode = \'stream\''
            },
            'g_load_balaner': {
                'path': f'{pgpool_path}/pgpool.conf',
                'old': '#sr_check_user = \'nobody\'',
                'new': 'sr_check_user = \'replica\''
            },
            'g_load_balaner': {
                'path': f'{pgpool_path}/pgpool.conf',
                'old': '#sr_check_password = \'\'',
                'new': 'sr_check_password = \'replica_pass\''
            },
            'g_load_balaner': {
                'path': f'{pgpool_path}/pgpool.conf',
                'old': '#load_balance_mode = off',
                'new': 'load_balance_mode = on'
            },
            'g_load_balaner': {
                'path': f'{pgpool_path}/pgpool.conf',
                'old': '#failover_command = \'\'',
                'new': 'failover_command = \'/etc/pgpool2/failover.sh %d %H %P %R\''
            },
            'g_load_balaner': {
                'path': f'{pgpool_path}/pgpool.conf',
                'old': '#follow_master_command = \'\'',
                'new': 'follow_master_command = \'/etc/pgpool2/follow_master.sh %d %H %M %P %r\''
            }
        }

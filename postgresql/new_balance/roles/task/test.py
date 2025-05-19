from allta import VBox
from roles.vm_info import (PASSWORD, PGPOOL_CONFIG_PATH, PGPOOL_HOSTNAME,
                           PGPOOL_PCP_USER, USERNAME, POSTGRES_DATA_PATH,
                           VERSION_PG, VMS_DATES, VMS_GROUPS, POSTGRES_PORT)


class Test:
    def __init__(self):
        self.provider = VBox()

    def test(self):
        provider = self.provider

        scp = {
            'database3': {
                'mode': 'push',
                'path_host': './roles/task/template/clients.py',
                'path_vm': '/tmp/clients.py'
            }
        }

        provider.scp(scp_settings=scp, vms_dates=VMS_DATES,
                     username=USERNAME, password=PASSWORD)
        
        postgres_data_path = POSTGRES_DATA_PATH
        test = {
            "database3": {
                "start test": {
                    "command": "sudo chmod 777 /tmp/clients.py && python3 /tmp/clients.py",
                    "signal set": "",
                    "signal get": ''
                },
            },
            "g_load_balancer":{
                'disable autofailback':{
                    "command": f'sleep 10 & sudo sed -i "s@auto_failback = on@auto_failback = off@g" {PGPOOL_CONFIG_PATH}',
                    "signal set": 'disable auto failback',
                    "signal get": ''                 
                },
                'disable load balancing db1':{
                    "command": f'sudo sed -i "s@backend_weight1 = 1@backend_weight1 = 0@g" {PGPOOL_CONFIG_PATH}',
                    "signal set": 'disable load balancing db1',
                    "signal get": ['disable auto failback']
                },
                'disable load balancing db2':{
                    "command": f'sudo sed -i "s@backend_weight2 = 1@backend_weight2 = 0@g" {PGPOOL_CONFIG_PATH}',
                    "signal set": 'disable load balancing db2',
                    "signal get": ['disable load balancing db1']
                },
                'enable load balancing db1':{
                    "command": f'sudo sed -i "s@backend_weight1 = 0@backend_weight1 = 1@g" {PGPOOL_CONFIG_PATH}',
                    "signal set": 'enable load balancing db1',
                    "signal get": ['database2', 'standby start']
                },
                'enable load balancing db2':{
                    "command": f'sudo sed -i "s@backend_weight2 = 0@backend_weight2 = 1@g" {PGPOOL_CONFIG_PATH}',
                    "signal set": 'enable load balancing db2',
                    "signal get": ['enable load balancing db1']
                },       


                'disable load balancing db0':{
                    "command": f'sudo sed -i "s@backend_weight0 = 1@backend_weight0 = 0@g" {PGPOOL_CONFIG_PATH}',
                    "signal set": 'disable load balancing db0',
                    "signal get": ['lbdb1', 'promote']
                },                 
                'enable load balancing db0':{
                    "command": f'sudo sed -i "s@backend_weight0 = 0@backend_weight0 = 1@g" {PGPOOL_CONFIG_PATH}',
                    "signal set": 'enable load balancing db0',
                    "signal get": ['database1', 'old master start']
                },

                'enable autofailback':{
                    "command": f'sudo sed -i "s@auto_failback = off@auto_failback = on@g" {PGPOOL_CONFIG_PATH}',
                    "signal set": 'enable auto failback',
                    "signal get": ['enable load balancing db0' ]                   
                },                  
            },                
            "lbdb1":{
                "reload conf":{
                    "command": f'sudo PCPPASSFILE=/tmp/.pcppass pcp_reload_config -w -h {PGPOOL_HOSTNAME} -U {PGPOOL_PCP_USER} --scope=cluster',
                    "signal set": 'reload conf1',
                    "signal get": ['disable load balancing db2']
                },             
                "detach_db1":{ #
                    "command": f'sudo PCPPASSFILE=/tmp/.pcppass pcp_detach_node -w -U {PGPOOL_PCP_USER} -h {PGPOOL_HOSTNAME} 1',
                    "signal set": 'detach db1',
                    "signal get":['reload conf1']                   
                },
                "detach_db2":{ #
                    "command": f'sudo PCPPASSFILE=/tmp/.pcppass pcp_detach_node -w -U {PGPOOL_PCP_USER} -h {PGPOOL_HOSTNAME} 2',
                    "signal set": 'detach db2',
                    "signal get": ['detach db1']                  
                },

                "reload conf2":{
                    "command": f'sleep 10 && sudo PCPPASSFILE=/tmp/.pcppass pcp_reload_config -w -h {PGPOOL_HOSTNAME} -U {PGPOOL_PCP_USER} --scope=cluster',
                    "signal set": 'reload conf2',
                    "signal get": ['enable load balancing db2']
                },                   
                "atach_db1":{
                    "command": f'sudo LC_ALL=C LANG=C PCPPASSFILE=/tmp/.pcppass pcp_attach_node -w -U {PGPOOL_PCP_USER} -h {PGPOOL_HOSTNAME} 1',
                    "signal set": 'attach1',
                    "signal get": ['reload conf2']                  
                },                
                "atach_db2":{
                    "command": f'sudo LC_ALL=C LANG=C PCPPASSFILE=/tmp/.pcppass pcp_attach_node -w -U {PGPOOL_PCP_USER} -h {PGPOOL_HOSTNAME} 2',
                    "signal set": 'attach2',
                    "signal get": ['attach1']                  
                },


                "promote new master":{
                    "command": f'sleep 10 && sudo PCPPASSFILE=/tmp/.pcppass pcp_promote_node -w -v --switchover -U {PGPOOL_PCP_USER} -h {PGPOOL_HOSTNAME} 1',
                    "signal set": 'promote1',
                    "signal get": ['attach2' ]                   
                },
                # "follow standby to new master":{
                #     "command": f'sleep 30 && sudo /tmp/follow.sh 0 {VMS_DATES['database1']['ip_bridge']} {POSTGRES_PORT} {postgres_data_path} 1 {VMS_DATES['database2']['ip_bridge']} 0 0 {POSTGRES_PORT} {postgres_data_path} && sudo /tmp/follow.sh 2 {VMS_DATES['database3']['ip_bridge']} {POSTGRES_PORT} {postgres_data_path} 1 {VMS_DATES['database2']['ip_bridge']} 0 0 {POSTGRES_PORT} {postgres_data_path}',
                #     "signal set": 'promote2',
                #     "signal get": ['promote1' ]                   
                # },
                "wait finally promote":{
                    "command": f'sleep 20',
                    "signal set": 'promote',
                    "signal get": ['promote1' ]                   
                },
                "reload conf3":{
                    "command": f'sudo PCPPASSFILE=/tmp/.pcppass pcp_reload_config -w -h {PGPOOL_HOSTNAME} -U {PGPOOL_PCP_USER} --scope=cluster',
                    "signal set": 'reload conf3',
                    "signal get": ['disable load balancing db0']
                }, 

                "detach_db0":{
                    "command": f'sudo PCPPASSFILE=/tmp/.pcppass pcp_detach_node -w -U {PGPOOL_PCP_USER} -h {PGPOOL_HOSTNAME} 0',
                    "signal set": 'detach db0',
                    "signal get": ['reload conf3' ]                   
                },
                "reload conf4":{
                    "command": f'sudo PCPPASSFILE=/tmp/.pcppass pcp_reload_config -w -h {PGPOOL_HOSTNAME} -U {PGPOOL_PCP_USER} --scope=cluster',
                    "signal set": 'reload conf4',
                    "signal get": ['enable auto failback']
                },                 
                "atach_db0":{
                    "command": f'sudo LC_ALL=C LANG=C PCPPASSFILE=/tmp/.pcppass pcp_attach_node -w -U {PGPOOL_PCP_USER} -h {PGPOOL_HOSTNAME} 0',
                    "signal set": '',
                    "signal get": ['reload conf4' ]                   
                },                
            },
            'g_replica':{
                'stop db':{
                    "command": f'sudo systemctl stop postgresql@{VERSION_PG}-contrprimer',
                    "signal set": 'standby stop',
                    "signal get": ["lbdb1", 'detach db2']    
                },                
                'update postgres':{
                    "command": 'sudo apt-get reinstall postgresql -y',
                    "signal set": 'standby reinstall',
                    "signal get": ['standby stop']  
                },
                'start db':{
                    "command": f'sudo systemctl start postgresql@{VERSION_PG}-contrprimer',
                    "signal set": 'standby start',
                    "signal get": ['standby reinstall'] 
                },                  
            },
            'database1':{
                'stop db':{
                    "command": f'sudo systemctl stop postgresql@{VERSION_PG}-contrprimer',
                    "signal set": 'old master stop',
                    "signal get": ["lbdb1", 'detach db0' ]    
                },                
                'update postgres':{
                    "command": 'sudo apt-get reinstall postgresql -y',
                    "signal set": 'old master reinstall',
                    "signal get": ['old master stop' ] 
                },
                # 'filling postgres':{
                #     "command": f'sleep 10 && sudo rm -rf {postgres_data_path} && sudo mkdir {postgres_data_path} && sudo chown postgres:postgres {postgres_data_path} && sudo chmod 750 {postgres_data_path} && sudo su - postgres -c "pg_basebackup -h {VMS_DATES['database2']['ip_bridge']} -p {POSTGRES_PORT} -U postgres -D {postgres_data_path} -Fp -Xs -P -R --wal-method=stream"',
                #     "signal set": 'old master filling',
                #     "signal get": ['old master reinstall' ] 
                # },                
                'start db':{
                    "command": f'sudo systemctl start postgresql@{VERSION_PG}-contrprimer',
                    "signal set": 'old master start',
                    "signal get": ['old master reinstall' ]
                },                  
            }
        }
        provider.execute(commands=test, vms_dates=VMS_DATES,
                         vms_groups=VMS_GROUPS, username=USERNAME, password=PASSWORD)

    def get_result(self):
        provider = self.provider
        scp = {
            'database3': {
                'mode': 'pull',
                'path_host': 'results_balance.txt',
                'path_vm': '/home/u/results_balance.txt'
            }
        }
        provider.scp(scp_settings=scp, vms_dates=VMS_DATES,
                username=USERNAME, password=PASSWORD)
        
        scp = {
            'database3': {
                'mode': 'pull',
                'path_host': 'available_packages.txt',
                'path_vm': '/home/u/available_packages.txt'
            }
        }
        provider.scp(scp_settings=scp, vms_dates=VMS_DATES,
                username=USERNAME, password=PASSWORD)
        scp = {
            'database3': {
                'mode': 'pull',
                'path_host': 'psb_info.txt',
                'path_vm': '/home/u/psb_info.txt'
            }
        }
        provider.scp(scp_settings=scp, vms_dates=VMS_DATES,
                    username=USERNAME, password=PASSWORD)





# sudo PCPPASSFILE=/tmp/.pcppass pcp_node_info -h pgpool.balance.rbt -p 9898 -U pgpool -w
# sudo PCPPASSFILE=/tmp/.pcppass pcp_promote_node  -w -v --switchover -U pgpool -h pgpool.balance.rbt 2 
# sudo PCPPASSFILE=/tmp/.pcppass pcp_attach_node -h pgpool.balance.rbt -p 9898 -U pgpool -w 2

# sudo /tmp/follow.sh 0 10.177.103.111 5440 /var/lib/postgresql/11/contrprimer 1 10.177.103.112 0 0 5440 /var/lib/postgresql/11/contrprimer
# sudo /tmp/follow.sh 2 10.177.103.113 5440 /var/lib/postgresql/11/contrprimer 1 10.177.103.112 0 0 5440 /var/lib/postgresql/11/contrprimer


# /tmp/failover.sh 0 10.177.103.111 5440 /var/lib/postgresql/15/contrprimer 2 10.177.103.113 0 0 5440 /var/lib/postgresql/15/contrprimer 10.177.103.113 0
from allta import VBox

from roles.vm_info import VERSION_PG, VMS_DATES, USERNAME, PASSWORD, VMS_GROUPS, POSTGRES_DATA_PATH, POSTGRES_PORT, PGPOOL_PCP_USER, PGPOOL_HOSTNAME


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

        standby = f'sudo su -c \'printf "127.0.0.1:9898:pgpool:1\n" > /tmp/.pcppass && \
                        sudo chmod 600 /tmp/.pcppass && \
                        sudo PCPPASSFILE=/tmp/.pcppass pcp_reload_config -h {PGPOOL_HOSTNAME} -p 9898 -U pgpool -w\' \
        '

        test = {
            "database3": {
                "set permission": {
                    "command": f"sudo su - postgres -c \"psql -h {PGPOOL_HOSTNAME} -p {POSTGRES_PORT} -c \'ALTER SCHEMA public OWNER TO  user0\'\"",
                    "signal set": "permission",
                    "signal get": ""
                },                
                "pgbench manual": {
                    "command": f"pgbench -i -s 100 -h {PGPOOL_HOSTNAME} -p {POSTGRES_PORT} -U postgres contrprimer",
                    "signal set": "pgbench manual",
                    "signal get": "permission"
                },
                "set chmod": {
                    "command": "sudo chmod 777 /tmp/clients.py",
                    "signal set": "chmod",
                    "signal get": ["database3", "pgbench manual"]
                },
                "start test": {
                    "command": "python3 /tmp/clients.py &",
                    "signal set": "",
                    "signal get": ["database3", "chmod"]
                },
            },
            "g_load_balancer":{
                'disable autofailback':{
                    "command": 'sudo sed -i "s@auto_failback = on@auto_failback = off@g"',
                    "signal set": 'disable auto failback',
                    "signal get": ''                    
                },
                'disable load balancing db1':{
                    "command": 'sudo sed -i "s@backend_weight1 = 1@backend_weight1 = 0@g"',
                    "signal set": 'disable load balancing db1',
                    "signal get": 'disable auto failback'
                },
                'disable load balancing db2':{
                    "command": 'sudo sed -i "s@backend_weight2 = 1@backend_weight2 = 0@g"',
                    "signal set": 'disable load balancing db2',
                    "signal get": 'disable load balancing db1'
                },       
                'enable load balancing db1':{
                    "command": 'sudo sed -i "s@backend_weight1 = 0@backend_weight1 = 1@g"',
                    "signal set": 'enable load balancing db1',
                    "signal get": 'enable auto failback'
                },
                'enable load balancing db2':{
                    "command": 'sudo sed -i "s@backend_weight2 = 0@backend_weight2 = 1@g"',
                    "signal set": 'enable load balancing db2',
                    "signal get": 'enable load balancing db1'
                },       


                'disable load balancing db0':{
                    "command": 'sudo sed -i "s@backend_weight0 = 1@backend_weight0 = 0@g"',
                    "signal set": 'disable load balancing db0',
                    "signal get": 'promote'
                },                 
                'enable load balancing db0':{
                    "command": 'sudo sed -i "s@backend_weight0 = 0@backend_weight0 = 1@g"',
                    "signal set": 'enable load balancing db0',
                    "signal get": ['database1', 'old master start']
                },

                'enable autofailback':{
                    "command": 'sudo sed -i "s@auto_failback = off@auto_failback = on@g"',
                    "signal set": 'enable auto failback',
                    "signal get": 'enable load balancing db0'                    
                },                  
            },                
            "lbdb1":{
                "reload conf":{
                    "command": f'sudo PCPPASSFILE=/tmp/.pcppass pcp_reload_config -w -h {PGPOOL_HOSTNAME} -u {PGPOOL_PCP_USER} --scope=cluster',
                    "signal set": 'reload conf1',
                    "signal get": 'disable load balancing db2'
                },             
                "detach_db1":{
                    "command": f'sudo PCPPASSFILE=/tmp/.pcppass pcp_detach_node -w -U {PGPOOL_PCP_USER} -h {PGPOOL_HOSTNAME} 1',
                    "signal set": '',
                    "signal get": 'reload conf1'                    
                },
                "detach_db2":{
                    "command": f'sudo PCPPASSFILE=/tmp/.pcppass pcp_detach_node -w -U {PGPOOL_PCP_USER} -h {PGPOOL_HOSTNAME} 2',
                    "signal set": '',
                    "signal get": 'reload conf1'                    
                },

                "reload conf2":{
                    "command": f'sudo PCPPASSFILE=/tmp/.pcppass pcp_reload_config -w -h {PGPOOL_HOSTNAME} -u {PGPOOL_PCP_USER} --scope=cluster',
                    "signal set": 'reload conf2',
                    "signal get": 'enable load balancing db2'
                },                   
                "atach_db1":{
                    "command": f'sudo PCPPASSFILE=/tmp/.pcppass pcp_recovery_node -w -U {PGPOOL_PCP_USER} -h {PGPOOL_HOSTNAME} 1',
                    "signal set": 'attach1',
                    "signal get": 'reload conf2'                    
                },                
                "atach_db2":{
                    "command": f'sudo PCPPASSFILE=/tmp/.pcppass pcp_recovery_node -w -U {PGPOOL_PCP_USER} -h {PGPOOL_HOSTNAME} 2',
                    "signal set": 'attach2',
                    "signal get": 'attach1'                    
                },


                "promote new master":{
                    "command": f'sudo PCPPASSFILE=/tmp/.pcppass pcp_promote_node -w -v --switchover -U {PGPOOL_PCP_USER} -h {PGPOOL_HOSTNAME} 1',
                    "signal set": 'promote',
                    "signal get": 'attach2'                    
                },


                "reload conf3":{
                    "command": f'sudo PCPPASSFILE=/tmp/.pcppass pcp_reload_config -w -h {PGPOOL_HOSTNAME} -u {PGPOOL_PCP_USER} --scope=cluster',
                    "signal set": 'reload conf3',
                    "signal get": 'disable load balancing db0'
                }, 

                "detach_db0":{
                    "command": f'sudo PCPPASSFILE=/tmp/.pcppass pcp_detach_node -w -U {PGPOOL_PCP_USER} -h {PGPOOL_HOSTNAME} 0',
                    "signal set": '',
                    "signal get": 'reload conf1'                    
                },
                "reload conf4":{
                    "command": f'sudo PCPPASSFILE=/tmp/.pcppass pcp_reload_config -w -h {PGPOOL_HOSTNAME} -u {PGPOOL_PCP_USER} --scope=cluster',
                    "signal set": 'reload conf4',
                    "signal get": 'enable auto failback'
                },                 
                "atach_db0":{
                    "command": f'sudo PCPPASSFILE=/tmp/.pcppass pcp_recovery_node -w -U {PGPOOL_PCP_USER} -h {PGPOOL_HOSTNAME} 0',
                    "signal set": '',
                    "signal get": 'reload conf2'                    
                },                
            },
            'g_replica':{
                'stop db':{
                    "command": f'sudo systemctl stop postgresql@{VERSION_PG}-contrprimer',
                    "signal set": 'standby stop',
                    "signal get": ["lbdb1", 'reload conf1' ]    
                },                
                'update postgres':{
                    "command": 'sudo apt reinstall postgresql -y',
                    "signal set": 'standby reinstall',
                    "signal get": 'standby stop '  
                },
                'start db':{
                    "command": f'sudo systemctl stop postgresql@{VERSION_PG}-contrprimer',
                    "signal set": 'standby start',
                    "signal get": 'standby reinstall' 
                },                  
            },
            'database1':{
                'stop db':{
                    "command": f'sudo systemctl stop postgresql@{VERSION_PG}-contrprimer',
                    "signal set": 'old master stop',
                    "signal get": ["lbdb1", 'reload conf3' ]    
                },                
                'update postgres':{
                    "command": 'sudo apt reinstall postgresql -y',
                    "signal set": 'old master reinstall',
                    "signal get": 'old master stop '  
                },
                'start db':{
                    "command": f'sudo systemctl stop postgresql@{VERSION_PG}-contrprimer',
                    "signal set": 'old master start',
                    "signal get": 'old master reinstall' 
                },                  
            }


        }
        provider.execute(commands=test, vms_dates=VMS_DATES,
                         vms_groups=VMS_GROUPS, username=USERNAME, password=PASSWORD)

# sudo PCPPASSFILE=/tmp/.pcppass pcp_node_info -h 127.0.0.1 -p 9898 -U pgpool -w'
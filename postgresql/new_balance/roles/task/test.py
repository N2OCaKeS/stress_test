from allta import VBoxManager

from roles.vm_info import VERSION_PG, VMS_DATES, USERNAME, PASSWORD, VMS_GROUPS

class Test:
    def __init__(self):
        self.provider = VBoxManager()

    def test(self):
        provider = self.provider

        scp = {
            'database3': {
                'mode': 'push',
                'path_host': './roles/task/template/clients.py',
                'path_vm': '/tmp/clients.py'
            }
        }

        provider.scp(scp_settings=scp, vms_dates=VMS_DATES, username=USERNAME, password=PASSWORD)

        # test = {
        #     'database3': {
        #         'pgbench manual':{
        #             'command':'pgbench -i -s 10 -h 10.177.103.131 -p 5440 -U postgres contrprimer',
        #             'signal set':'pgbench manual',
        #             'signal get':''                             
        #         },
        #         'set chmod': {
        #             'command':'sudo chmod 777 /tmp/clients.py',
        #             'signal set':'chmod',
        #             'signal get':['database3', 'pgbench manual']      
        #         },
        #         'start test': {
        #             'command':'python3 /tmp/clients.py',
        #             'signal set':'',
        #             'signal get':['database3', 'chmod' ]
        #         },
        #     },
        #     'database1': {
        #         'reinstall postgres': {
        #             'command':f'sleep 5 && sudo apt reinstall postgresql-{VERSION_PG} -y',
        #             'signal set':'Reinstall',
        #             'signal get':['database3', 'chmod' ]                    
        #         },
        #         'start postgres dp': {
        #             'command':f'sudo systemctl start postgresql@{VERSION_PG}-contrprimer',
        #             'signal set':'',
        #             'signal get':['database1','Reinstall']                  
        #         }
        #     }
        # }

        test = {
    "database3": {
        "pgbench manual": {
            "command": "pgbench -i -s 10 -h 10.177.103.131 -p 5440 -U postgres contrprimer",
            "signal set": "pgbench manual",
            "signal get": ""
        },
        "set chmod": {
            "command": "sudo chmod 777 /tmp/clients.py",
            "signal set": "chmod",
            "signal get": ["database3", "pgbench manual"]
        },
        "start test": {
            "command": "python3 /tmp/clients.py",
            "signal set": "",
            "signal get": ["database3", "chmod"]
        }
    },
    "database1": {
        "reinstall postgres": {
            "command": f"""sleep 5 && \
sudo systemctl stop postgresql@{VERSION_PG}-contrprimer && \
sudo rm -rf /var/lib/postgresql/{VERSION_PG}/contrprimer/* && \
sudo apt reinstall postgresql-{VERSION_PG} -y""",
            "signal set": "Reinstall",
            "signal get": ["database3", "chmod"]
        },
        "start postgres dp": {
            "command": f"sudo systemctl start postgresql@{VERSION_PG}-contrprimer",
            "signal set": "",
            "signal get": ["database1", "Reinstall"]
        }
    }
}
        provider.execute(commands=test, vms_dates=VMS_DATES, vms_groups=VMS_GROUPS, username=USERNAME, password=PASSWORD)

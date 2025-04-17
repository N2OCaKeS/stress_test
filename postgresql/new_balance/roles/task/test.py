from allta import VBox

from roles.vm_info import VERSION_PG, VMS_DATES, USERNAME, PASSWORD, VMS_GROUPS, POSTGRES_DATA_PATH, POSTGRES_PORT


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
                    "command": "pgbench -i -s 100 -h 10.177.103.131 -p 5440 -U postgres contrprimer",
                    "signal set": "pgbench manual",
                    "signal get": ""
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
                "recovery db": {
                    "command": 'sudo su -c \'printf "127.0.0.1:9898:pgpool:1\n" > ~/.pcppass && sudo chmod 600 ~/.pcppass && sudo PCPPASSFILE=~/.pcppass pcp_attach_node -h 127.0.0.1 -p 9898 -U pgpool -n 0\'',
                    "signal set": "",
                    "signal get": ["database1", "return"]
                },                

            },
            "database1": {
                "reinstall postgres": {
                    "command": f"""sleep 40 && \
sudo systemctl stop postgresql@{VERSION_PG}-contrprimer && \
sudo rm -rf /var/lib/postgresql/{VERSION_PG}/contrprimer/* && \
sudo apt reinstall postgresql-{VERSION_PG} -y""",
                    "signal set": "Reinstall",
                    "signal get": ["database3", "chmod"]
                },
                "return db to cluster as replica": {
                    "command": f'sudo rm -rf {POSTGRES_DATA_PATH} && sudo su - postgres -c "pg_basebackup -h {VMS_DATES['database2']['ip_bridge']} -p {POSTGRES_PORT} -U postgres -D {POSTGRES_DATA_PATH} -Fp -Xs -P -R" && ',
                    "signal set": "return",
                    "signal get": ["database1", "Reinstall"]
                }
            }
        }
        provider.execute(commands=test, vms_dates=VMS_DATES,
                         vms_groups=VMS_GROUPS, username=USERNAME, password=PASSWORD)

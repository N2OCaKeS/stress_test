from allta import VBoxManager

from roles.vm_info import VERSION_PG, VMS_DATES, USERNAME, PASSWORD

class Test:
    def __init__(self):
        self.provider = VBoxManager()

    def test(self):
        provider = self.provider

        scp = {
            'database1': {
                'mode': 'push',
                'path_host': './roles/task/template/clients.py',
                'path_vm': '/tmp/clients.py'
            }
        }

        provider.scp(scp_settings=scp, vms_dates=VMS_DATES, username=USERNAME, password=PASSWORD)

        test = {
            'database3': {
                'start test': {
                    'command':'sudo chmod 777 /tmp/clients.py',
                    'signal set':'',
                    'signal get':['database3', 'venv']       
                },
                'start test': {
                    'command':'python3 /tmp/clients.py',
                    'signal set':'',
                    'signal get':['database3', 'venv']       
                },
            },
            'database1': {
                'reinstall postgres': {
                    'command':f'sleep 5 && sudo apt reinstall postgresql-{VERSION_PG} -y',
                    'signal set':'Reinstall',
                    'signal get':['database3', 'venv']                       
                },
                'start postgres dp': {
                    'command':f'sudo systemctl start postgresql@{VERSION_PG}-contrprimer',
                    'signal set':'',
                    'signal get':['database1','Reinstall']                  
                }
            }
        }
        provider.execute(commands=test, vms_dates=VMS_DATES, vms_groups=VMS_GROUPS, username=USERNAME, password=PASSWORD)



from allta import VBoxManager

from roles.vm_info import VERSION_PG, VMS_DATES

class Test:
    def __init__(self):
        self.provider = VBoxManager()

    def test(self):
        provider = self.provider

        scp = {
            'database1': {
                'mode': 'push',
                'path_host': 'postgresql/new_balance/roles/task/template/clients.py',
                'path_vm': '/tmp/clients.py'
            }
        }

        provider.scp(scp, VMS_DATES)

        test = {
            'database3': {
                'create venv and install dependencies': {
                    'command':'cd /tmp && python3 -m venv venv && pip install psycopg2',
                    'signal set':'venv',
                    'signal get':['']       
                },
                'start test': {
                    'command':'cd /tmp && source venv/bin/activate && python /tmp/clients.py',
                    'signal set':'',
                    'signal get':['database3', 'venv']       
                },
            },
            'database1': {
                'reinstall postgres': {
                    'command':f'sleep 8 && sudo apt reinstall postgresql-{VERSION_PG} -y',
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

        provider.execute(VMS_DATES, test)


from allta import VBoxManager
from roles.vm_info import VERSION_PG, VMS_DATES, VMS_GROUPS, DOMAIN


class PreConfigure():
    def __init__(self):
        self.provider = VBoxManager()


    def provision(self):
        scp = { # TODO переписать путь
            'g_all': {
                'mode': 'push',
                'path_host': './provision/provision.sh',
                'path_vm': '/tmp/provision.sh'
            }
        }
        self.provider.scp(scp, VMS_DATES, VMS_GROUPS)
        task = {
            'g_all':{
                'chmod': {
                    'command': f'sudo chmod +x /tmp/provision.sh',
                    'signal set': 'chmod',
                    'signal get': ''
                },   
                'provision': {
                    'command': f'sudo bash /tmp/provision.sh',
                    'signal set': '',
                    'signal get': ['chmod']
                },             
            }
        }

        self.provider.execute(VMS_DATES, task, VMS_GROUPS)


    def apt_install(self):
        apt_install = {
            'g_domain_client': ['astra-freeipa-client'],
            'g_database': [f'postgresql-{VERSION_PG}', 'python3-venv'],
            'g_load_balancer': ['pgpool2'],
            'dcfreeipa': ['astra-freeipa-server']
        }
        self.provider.apt.install(
            apt_structure=apt_install, vm_dates=VMS_DATES, vms_groups=VMS_GROUPS)
        
    def set_hosts(self):
        self.provider.set_hosts(domain='balance.rbt', vms_dates=VMS_DATES, username='u', password='1')
        command = f'echo -e "10.177.103.131  pgpool.{DOMAIN} pgpool\n10.177.103.10   allta.devos.astralinux.ru allta" | tee -a /etc/hosts'
        hosts = {
            'g_all': {
                'set hosts': {
                    'command':f'sudo sh -c \'{command}\'',
                    'signal set':'',
                    'signal get':''
                }
            }
        }
        self.provider.execute(vm_dates=VMS_DATES, commands=hosts, vms_groups=VMS_GROUPS)

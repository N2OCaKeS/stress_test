from allta import VBoxManager
from roles.vm_info import VERSION_PG, VMS_DATES, VMS_GROUPS, DOMAIN


class PreConfigure():
    def __init__(self):
        self.provider = VBoxManager()

    def apt_install(self):
        apt_install = {
            'g_domain_client': ['astra-freeipa-client'],
            'g_database': [f'postgresql-{VERSION_PG}'],
            'g_load_balaner': ['pgpool2'],
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
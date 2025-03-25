from allta import VBoxManager
from roles.vm_info import VERSION_PG, VMS_DATES, VMS_GROUPS


class Apt():
    def __init__(self):
        self.provider = VBoxManager()

    def apt_install(self):
        apt_install = {
            'g_domain_client': ['astra-freeipa-client'],
            'g_database': [f'postgresql-{VERSION_PG}'],
            'g_load_balaner': ['pgpool2'],
            'domain': ['astra-freeipa-server']
        }
        self.provider.apt.install(
            apt_structure=apt_install, vm_dates=VMS_DATES, vms_groups=VMS_GROUPS)

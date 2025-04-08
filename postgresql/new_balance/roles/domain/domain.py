from allta import VBoxManager
from roles.vm_info import DOMAIN, DOMAIN_ADMIN_PASSWORD, DOMAIN_ADMIN_USER, DOMAIN_USER_PASSWORD, VMS_DATES, VMS_GROUPS


class DomainVM():

    def __init__(self):
        self.provider = VBoxManager()

    def settings(self):
        '''Полная настройка домена на всех ВМ'''
        provider = self.provider

        domain = {
            'settings': {
                'domain': DOMAIN,
                'admin_password': DOMAIN_ADMIN_PASSWORD
            },
            'domain': {
                'host': 'dcfreeipa', 
            },
            'client': {
                'host': 'g_domain_client', # g_ если начинается с такого префикса то это для группы хостов
            }
        }

        provider.freeipa(domain=domain, vm_dates=VMS_DATES, groups=VMS_GROUPS)

        tasks = {
            'dcfreeipa': {
                'kinit': {
                    'command': f'yes {DOMAIN_ADMIN_PASSWORD} | kinit {DOMAIN_ADMIN_USER}',
                    'signal set': 'Kinit',
                    'signal get': '',
                },
            }
        }

        # генератор словаря создает однотипные задачи в словарь
        for n in range(3):
            tasks['dcfreeipa'][f'create user{n}'] = {
                'command': f'yes {DOMAIN_USER_PASSWORD}| ipa user-add user{n} --first=user{n} --last=user{n} --macmin=0 --macmax=3 --miclevel=63 --password --password-expiration="2099-12-31Z"',
                'signal set': '',
                'signal get': ['dcfreeipa' ,'Kinit']
            }
            tasks['dcfreeipa'][f'register database{n+1}'] = {
            'command': f'ipa service-add postgres/database{n+1}.{DOMAIN}@{DOMAIN.upper()}',
            'signal set': '',
            'signal get': ['dcfreeipa' ,'Kinit']
            }

        provider.execute(vm_dates=VMS_DATES, commands=tasks, vms_groups=VMS_GROUPS)


from allta import VBoxManager
from roles.vm_info import DOMAIN, DOMAIN_ADMIN_PASSWORD, DOMAIN_ADMIN_USER, DOMAIN_USER_PASSWORD, VMS_DATES, VMS_GROUPS


class DomainVM(): # TODO НАДО ПРОВЕРИТЬ!

    def __init__(self):
        self.provider = VBoxManager()

    def settings(self):
        '''Полная настройка домена на всех ВМ'''
        provider = self.provider
        tasks = {
            'domain': {
                'domain_init': {
                    'command': f'sudo astra-freeipa-server -d {DOMAIN} -p {DOMAIN_ADMIN_PASSWORD} -o -y',
                    'signal set': '',
                    'signal get': ''
                },
                'reboot': {  # TODO реализовать в библиотеки обработку если имя задачи reboot то послать сигнал перезагрузки ВМ и ждать пока она не запуститься после чего продолжить выполнение
                    'command': '',
                    'signal set': '',
                    'signal get': '',
                },
                'kinit': {  # TODO реализовать в библиотеки обработку если имя задачи reboot то послать сигнал перезагрузки ВМ и ждать пока она не запуститься после чего продолжить выполнение
                    'command': f'yes {DOMAIN_ADMIN_PASSWORD} | kinit {DOMAIN_ADMIN_USER}',
                    'signal set': '',
                    'signal get': '',
                },
            },
            'g_domain_client': {
                'client join domain': {
                    'command': f'sudo astra-freeipa-client -d ipa.rbt -p {DOMAIN_ADMIN_PASSWORD} -y',
                    'signal set': '',
                    'signal get': '',
                },
                'reboot': {  # TODO реализовать в библиотеки обработку если имя задачи reboot то послать сигнал перезагрузки ВМ и ждать пока она не запуститься после чего продолжить выполнение
                    'command': '',
                    'signal set': '',
                    'signal get': '',
                }
            }
        }

        # генератор словаря создает однотипные задачи в словарь
        for n in range(3):
            tasks['domain'][f'create user{n}'] = {
                'command': f'yes {DOMAIN_USER_PASSWORD}| ipa user-add user{n} --first=user{n} --last=user{n} --macmin=0 --macmax=3 --miclevel=63 --password --password-expiration="2099-12-31Z"',
                'signal set': '',
                'signal get': ''
            }
        provider.execute(vm_dates=VMS_DATES, commands=tasks, vms_groups=VMS_GROUPS, username='u', password='1')
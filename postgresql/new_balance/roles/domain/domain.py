from vm_info import DOMAIN, DOMAIN_ADMIN_PASSWORD

class DomainVM():
    """Настройка всего что связано с доменом для теста"""
    def settings():
        
        tasks = {
            'domain': {
                'domain_init':{
                    'command':f'yes {DOMAIN_ADMIN_PASSWORD} | sudo astra-freeipa-server -d {DOMAIN} -y',
                    'signal set':'',
                    'signal get':''
                },
                'reboot':{ # TODO реализовать в библиотеки обработку если имя задачи reboot то послать сигнал перезагрузки ВМ и ждать пока она не запуститься после чего продолжить выполнение
                    'command':'',
                    'signal set':'',
                    'signal get':'',                    
                }
            },
            'domain_client':{
                'client join domain':{
                    'command':'',
                    'signal set':'',
                    'signal get':'',
                }
            }
        }

        # генератор словаря создает однотипные задачи в словарь
        i = 3
        for n in range(i):
            tasks['domain'][f'create user{n}'] = {
                'command': f'',
                'signal set': '',
                'signal get': ''
            }
            tasks['domain'][f'give privilege user :{n}'] = {
                'command': f'',
                'signal set': '',
                'signal get': ''
            }
        
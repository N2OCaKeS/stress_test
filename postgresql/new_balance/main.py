
from allta import VBoxManager


from roles.task.pre_configure import PreConfigure
from roles.domain.domain import DomainVM
from roles.database.db import DatabaseVM

def main():
    vagrant_path = './'
    prepare_path = './prepare/prepare.sh'

    provider = VBoxManager()
#    provider.prepare(prepare_path)
#    provider.build(vagrant_path, f'1.8.0.s', '1.8.0', VMS, VMS_DATES) # TODO Настроить вместо фиксированных значений 

#     print('Ожидаем 2 мин перед началом теста')
#    sleep(120)

    # configure = PreConfigure()
    # configure.set_hosts()
    # configure.apt_install() # Проверено работает

    # domain = DomainVM ()
    # domain.settings() # ПЕРЕПРОВЕРИТЬ

    database = DatabaseVM()
    database.settings()

if __name__ == "__main__":
    main()
from time import sleep
from allta import VBoxManager
from roles.vm_info import VMS, VMS_DATES

from roles.task.pre_configure import PreConfigure
from roles.domain.domain import DomainVM
from roles.database.db import DatabaseVM

vagrant_path = './'
provider = VBoxManager()
provider.build(vagrant_path, '1.8.0.s', '1.8.0', VMS, VMS_DATES) # TODO Понять почему после сборки ВМ не доступны через shh по ip bridge

print('Ожидаем 2 мин перед началом теста')
sleep(120)

configure = PreConfigure()
configure.set_hosts()
configure.apt_install() # Проверено работает

domain = DomainVM ()
domain.settings() 

database = DatabaseVM()
database.settings()


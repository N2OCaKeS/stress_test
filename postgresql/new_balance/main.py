from allta import VBoxManager
from roles.vm_info import VMS, VMS_DATES

from roles.task.apt import Apt
from roles.domain.domain import DomainVM
from roles.database.db import DatabaseVM

vagrant_path = './'
provider = VBoxManager()
provider.build(vagrant_path, '1.8.0.s', '1.8.0', VMS, VMS_DATES) # TODO Понять почему после сборки ВМ не доступны через shh по ip bridge

apt = Apt()
apt.apt_install()

domain = DomainVM ()
domain.settings()

database = DatabaseVM()
database.settings()


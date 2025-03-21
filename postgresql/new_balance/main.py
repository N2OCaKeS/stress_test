from allta import VBoxManager
from roles.role_settings import vms_groups, vms, vms_dates, domain

from task.apt import apt_install





provision = './provision/provision.sh'
prepare = './prepare/prepare.sh'
vagrant_path = './'

prov = VBoxManager()
# prov.prepare (prepare)
prov.build(vagrant_path, '1.8.0.s', '1.8.0', vms, vms_dates, provision) # TODO Понять почему после сборки ВМ не доступны через shh по ip bridge
#prov.apt.install(apt_install, vms_dates, vms_groups)
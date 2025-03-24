from allta import VBoxManager
from postgresql.new_balance.roles.vm_info import VMS_GROUPS, VMS, VMS_DATES, DOMAIN

vagrant_path = './'

prov = VBoxManager()
prov.build(vagrant_path, '1.8.0.s', '1.8.0', VMS, VMS_DATES) # TODO Понять почему после сборки ВМ не доступны через shh по ip bridge
# prov.apt.install(apt_install, vms_dates, vms_groups)
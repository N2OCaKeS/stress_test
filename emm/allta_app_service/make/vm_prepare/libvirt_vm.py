from allta import Libvirt, LibvirtManager, SystemCommands
from time import sleep


Libvirt.prepare()
kernel = "5.10.190-1-generic"
rc = "1.7.5"

vms = {  # Полный список ВМ
    'virtual-station-17-1': {'host-port': '22',
                  'ip': '10.0.0.11',
                  'sshnum': '',
                  'ip_bridge': '10.177.103.101',
                  'cpu': '16',
                  'ram': '131072'},
    'virtual-station-17-2': {'host-port': '22',
                  'ip': '10.0.0.12',
                  'sshnum': '1',
                  'ip_bridge': '10.177.103.102',
                  'cpu': '16',
                  'ram': '131072'},
    'virtual-station-17-3': {'host-port': '22',
                  'ip': '10.0.0.13',
                  'sshnum': '2',
                  'ip_bridge': '10.177.103.103',
                  'cpu': '16',
                  'ram': '131072'},
    'virtual-station-17-4': {'host-port': '22',
              'ip': '10.0.0.41',
              'sshnum': '3',
              'ip_bridge': '10.177.103.104',
              'cpu': '16',
              'ram': '131072'},
    'work-station1': {'host-port': '22',
              'ip': '10.0.0.41',
              'sshnum': '3',
              'ip_bridge': '10.177.103.201',
              'cpu': '16',
              'ram': '131072'},
    'work-station2': {'host-port': '22',
              'ip': '10.0.0.41',
              'sshnum': '3',
              'ip_bridge': '10.177.103.202',
              'cpu': '16',
              'ram': '131072'},              
}



vms_list = ['virtual-station-17-1', 'virtual-station-17-2', 'virtual-station-17-3', 'virtual-station-17-4', 'work-station1', 'work-station2']
group = {'all': vms_list}
Libvirt.prepare()
new_vms = Libvirt.build(box='vm_station1.7', rc=rc, vms_dates=vms, vms=vms_list)

scp_prepare = {
    "g_all": [
        {
            'mode': 'push',
            'path_host': './provision/env_provision.sh',
            'path_vm': '/home/u/env_provision.sh'            
        }
    ],        
}

Libvirt.scp(scp_settings=scp_prepare, vms_dates=new_vms, vms_groups=group, username='u', password='1')

prepare = {}

for vm_name in vms:
    prepare[vm_name] = {
        'prepare': {
            'command': f"sudo chmod 777 /home/u/env_provision.sh && sudo su -c '/home/u/env_provision.sh {vm_name} {kernel} {rc}'",
            'signal set': 'prepare',
            'signal get': ''
        },
        'confirm': {
            'command': f"(sleep 2 && sudo shutdown -r now) &",
            'signal set': '',
            'signal get': ['prepare']
        },        
    }

Libvirt.execute(commands=prepare, vms_dates=new_vms, vms_groups=group, username='u', password='1')
sleep(20)

vms = {  # Полный список ВМ
    'virtual-station-18-1': {'host-port': '22',
                  'ip': '10.0.0.11',
                  'sshnum': '',
                  'ip_bridge': '10.177.103.105',
                  'cpu': '16',
                  'ram': '131072'},
    'virtual-station-18-2': {'host-port': '22',
                  'ip': '10.0.0.12',
                  'sshnum': '1',
                  'ip_bridge': '10.177.103.106',
                  'cpu': '16',
                  'ram': '131072'},
    'virtual-station-18-3': {'host-port': '22',
                  'ip': '10.0.0.13',
                  'sshnum': '2',
                  'ip_bridge': '10.177.103.107',
                  'cpu': '16',
                  'ram': '131072'},
    'virtual-station-18-4': {'host-port': '22',
              'ip': '10.0.0.41',
              'sshnum': '3',
              'ip_bridge': '10.177.103.108',
              'cpu': '16',
              'ram': '131072'},
}

vms_list = ['virtual-station-18-1', 'virtual-station-18-2', 'virtual-station-18-3', 'virtual-station-18-4']
group = {'all': vms_list}
rc = "1.8.1.6"
new_vms = Libvirt.build(box='vm_station1.8', rc=rc, vms_dates=vms, vms=vms_list)

scp_prepare = {
    "g_all": [
        {
            'mode': 'push',
            'path_host': './provision/env_provision.sh',
            'path_vm': '/home/u/env_provision.sh'            
        }
    ],        
}

Libvirt.scp(scp_settings=scp_prepare, vms_dates=new_vms, vms_groups=group, username='u', password='1')

prepare = {}

for vm_name in vms:
    prepare[vm_name] = {
        'prepare': {
            'command': f"sudo chmod 777 /home/u/env_provision.sh && sudo su -c '/home/u/env_provision.sh {vm_name} {kernel} {rc}'",
            'signal set': 'prepare',
            'signal get': ''
        },
        'confirm': {
            'command': f"(sleep 2 && sudo shutdown -r now) &",
            'signal set': '',
            'signal get': ['prepare']
        },        
    }

Libvirt.execute(commands=prepare, vms_dates=new_vms, vms_groups=group, username='u', password='1')
sleep(20)

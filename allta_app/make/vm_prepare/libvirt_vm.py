from allta import Libvirt, LibvirtManager, SystemCommands



Libvirt.prepare()
kernel = "5.10.190-1-generic"
rc = "1.7.5"

vms = {  # Полный список ВМ
    'virtual-station1': {'host-port': '22',
                  'ip': '10.0.0.11',
                  'sshnum': '',
                  'ip_bridge': '10.177.103.111',
                  'cpu': '2',
                  'ram': '4096'},
    'virtual-station2': {'host-port': '22',
                  'ip': '10.0.0.12',
                  'sshnum': '1',
                  'ip_bridge': '10.177.103.112',
                  'cpu': '12',
                  'ram': '256000'},
    'virtual-station3': {'host-port': '22',
                  'ip': '10.0.0.13',
                  'sshnum': '2',
                  'ip_bridge': '10.177.103.113',
                  'cpu': '12',
                  'ram': '256000'},
    'virtual-station4': {'host-port': '22',
              'ip': '10.0.0.41',
              'sshnum': '3',
              'ip_bridge': '10.177.103.141',
              'cpu': '12',
              'ram': '256000'}
}

vms_list = ['virtual-station1', 'virtual-station2', 'virtual-station3', 'virtual-station4']

group = {'all': ['virtual-station1', 'virtual-station2', 'virtual-station3', 'virtual-station4',]}
new_vms = Libvirt.build('1.7.5.o', '1.7.5', vms)

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
        'set hostname': {
            'command': f"sudo hostnamectl set-hostname {vm_name} && if grep -q '^127\\.0\\.1\\.1' /etc/hosts; then sudo sed -i 's/^127\\.0\\.1\\.1.*/127.0.1.1\\t{vm_name}/' /etc/hosts; else echo -e '127.0.1.1\\t{vm_name}' | sudo tee -a /etc/hosts; fi",
            'signal set': 'hostname',
            'signal get': ''
        },
        'prepare': {
            'command': f"sudo chmod 777 /home/u/env_provision.sh && sudo su -c '/home/u/env_provision.sh {vm_name} {kernel} {rc}'",
            'signal set': '',
            'signal get': ['hostname']
        }
    }
Libvirt.execute(commands=prepare, vms_dates=new_vms, vms_groups=group, username='u', password='1')

LibvirtManager.create_snapshot(vms_list, "Snapshot_1_7_5")


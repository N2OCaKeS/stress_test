import json
from datetime import datetime

from app.utils.config import settings
from app.utils.ssh import SimpleSSH
from app.utils.system_commands import System_Commands as system_comand


class VM:
    # Базовые действия с ВМ
    def __init__(self, username: str, password: str, server_ip: str, user_id: int, box: str):
        self.connection_string = f"-c qemu+ssh://{username}@{server_ip}/session "
        self.user_id = user_id
        self.ssh = SimpleSSH(host=server_ip, username=username, password=password, port=22)
        self.username = username
        self.box = box

    def _get_base_image(self):

        config_name = 'box-config.json'
        config = f'wget -P /tmp -O {config_name} {settings.IMAGE_CONFIG_URL}'
        self.ssh.run_command(command=config)
        with open('test-box-config.json', 'r') as r:
            dates = json.loads(r.read())

        for i in dates['libvirt_box']:
            if self.box in str(i):
                for key in i.keys():
                    if str(key).endswith('s'):
                        true_key = key
                        box_name = f"{i[true_key][0]}"
                        box_url = i[true_key][1]
                    if str(self.box).startswith('1.7'):
                        os_version = 'alse17'
                    elif str(self.box).startswith('1.8'):
                        os_version = 'alse17' # Возможно в будущем будет исправлено
        if not true_key:
            for i in dates['libvirt_box']:
                if str(self.box).startswith('1.7'):
                    if '1.7.5.s' in str(i):
                        box_name = f"{i['1.7.5.s'][0]}"
                        box_url = i['1.7.5.s'][1]
                    os_version = 'alse17'
                elif str(self.box).startswith('1.8'):
                    if '1.8.1.s' in str(i):
                        box_name = f"{i['1.8.1.s'][0]}"
                        box_url = i['1.8.1.s'][1]
                    os_version = 'alse17' # Возможно в будущем будет исправлено
        image = f'wget -P /tmp {box_url} && tar xzf /tmp/{box_name}.tar.gz -C /tmp/ && mv /tmp/{box_name}.qcow2 {settings.VMS_PATH}/{self.username}/{box_name}.qcow2'
        self.ssh.run_command(command=image)
        return box_name, os_version

    def create(self, version: str, vm_name: str, cpu: int, ram: int):
        box_name, os_version = self._get_base_image(version=version)
        disk_path = f'{settings.VMS_PATH}/{self.username}/{box_name}.qcow2'
        virt_install = f'virt-install {self.connection_string} -n {vm_name} --memory {ram} \
            --vcpus {cpu}  --import --disk path={disk_path} --os-variant {os_version} \
            --network network={self.username} --noautoconsole --noreboot --cpu host-model,+vmx --graphics none'
        print(system_comand.check_output_command(virt_install))
        get_vm_config = f'virsh {self.connection_string} dumpxml {vm_name} > /tmp/{vm_name}.xml'
        print(system_comand.check_output_command(get_vm_config))
        bridge_net = f'''
awk -v bridge="br0" '
BEGIN {{ins=0}}
/<devices>/ {{
    print
    print "    <interface type=\"bridge\">"
    print "      <source bridge=\""bridge"\"/>"
    print "      <model type=\"virtio\"/>"
    print "      <address type=\"pci\" domain=\"0x0000\" bus=\"0x01\" slot=\"0x00\" function=\"0x0\"/>"
    print "    </interface>"
    ins=1
    next
}}
/^ *<interface /, /^ *<\\/interface>/ {next}
{{print}}
' "{vm_name}.xml" > "${vm_name}.mod"
'''
        print(system_comand.check_output_command(bridge_net))
        set_bridge_net = f'virsh {self.connection_string} define /tmp/{vm_name}.mod'


    def delete(self, vm_name: str):
        command = f'virsh undefine --domain {vm_name} --delete-storage-volume-snapshots --remove-all-storage'
        print(system_comand.check_output_command(command))

    def list(self):
        command = f'virsh {self.connection_string} list --all'
        print(system_comand.check_output_command(command))

    # Включение выключение ВМ

    def poweron(self, vm_name):
        command = f'virsh {self.connection_string} start --domain {vm_name}'
        print(system_comand.check_output_command(command))        

    def poweroff(self, vm_name):
        command = f'virsh {self.connection_string} destroy --domain {vm_name}'
        print(system_comand.check_output_command(command))        

    def reboot(self, vm_name):
        command = f'virsh {self.connection_string} reboot --domain {vm_name}'
        print(system_comand.check_output_command(command))        

    def save(self, vm_name):
        command = f'virsh {self.connection_string} save --domain {vm_name}'
        print(system_comand.check_output_command(command))        

    def resume(self, vm_name):
        command = f'virsh {self.connection_string} resume --domain {vm_name}'  
        print(system_comand.check_output_command(command))              



class Snapshot:

    def __init__(self, username: str, server_ip: str, user_id: int):
        self.connection_string = f"-c qemu+ssh://{username}@{server_ip}/session "
        self.user_id = user_id

    def create(self, vm_name: str, snapshot_name: str, snapshot_description: str = f"{datetime.now().strftime('%Y-%m-%d %H:%M')}"):
        command = f'virsh {self.connection_string} snapshot-create-as --domain {vm_name} --name {snapshot_name} --description {snapshot_description}'
        print(system_comand.check_output_command(command))

    def delete(self, vm_name: str, snapshot_name: str):
        command = f'virsh {self.connection_string} snapshot-delete --domain {vm_name} --snapshotname {snapshot_name}'        
        print(system_comand.check_output_command(command))

    def revert(self, vm_name: str, snapshot_name: str):
        command = f'virsh {self.connection_string} snapshot-revert --domain {vm_name} --snapshotname {snapshot_name}'        
        print(system_comand.check_output_command(command))

    def list(self, vm_name: str):
        command = f'virsh {self.connection_string} snapshot-list --domain {vm_name} --name'
        print(system_comand.check_output_command(command))

    def info(self, vm_name: str, snapshot_name: str):
        command = f'virsh {self.connection_string} snapshot-list --domain {vm_name} --snapshotname {snapshot_name}'
        print(system_comand.check_output_command(command))


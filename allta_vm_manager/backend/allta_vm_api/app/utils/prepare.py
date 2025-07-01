from os import getenv

from app.utils.config import settings
from app.utils.ssh import SimpleSSH
from app.utils.system_commands import System_Commands

class prepare:
    def prepare(server_ip: str, eth_dev: str):

        ssh = SimpleSSH(host=server_ip, username=settings.ADMIN_USERNAME, password=settings.ADMIN_PASSWORD, port=22)
        system_commands = System_Commands()

        # Install dependencies
        dependencies = "sudo apt-get install -y astra-kvm wget sshpass"
        ssh.run_command(dependencies)

        # Copy ssh keys
        copy_key = f'sshpass -p {settings.ADMIN_PASSWORD} ssh-copy-id {settings.ADMIN_USERNAME}@{server_ip}'
        system_commands(copy_key)        

        # Create path
        create_storage_path = f'sudo mkdir {settings.VMS_PATH} && sudo chmod 777 {settings.VMS_PATH}'
        ssh.run_command(create_storage_path)

        # Create user and give priveleges
        create_user = f' sudo usermod -aG kvm,libvirt,libvirt-qemu,libvirt-admin $USER '
        ssh.run_command(create_user)

        # Create user net
        create_net = f'virsh -c qemu:///system net-autostart default && virsh -c qemu:///system net-start default'
        ssh.run_command(create_net)

        # Create user storage
        create_user_storage = f'virsh -c qemu///system pool-define-as --name vms --type dir --target {settings.VMS_PATH}'
        ssh.run_command(create_user_storage)
        
        # Create br0 net
        create_br0_net = f'''
sudo tee /etc/network/interfaces > /dev/null <<EOF
auto lo
iface lo inet loopback

auto br0
iface br0 inet static
    address {server_ip}
    netmask 255.255.255.0
    gateway 10.177.103.254
    dns-nameservers 10.177.180.246 10.177.181.142
    bridge_ports {eth_dev}
    bridge_stp off
    bridge_fd 0
    bridge_maxwait 0

iface {eth_dev} inet manual
EOF
'''
        ssh.run_command(create_br0_net)

        pass
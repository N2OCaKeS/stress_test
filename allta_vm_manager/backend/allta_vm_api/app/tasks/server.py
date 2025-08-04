from celery_app import celery_app
from utils.ssh import SimpleSSH
from utils.scp import SCP

@celery_app.task
def init(host, username, password, bridge, phy_if):
    
    ssh = SimpleSSH(host=host, username=username, password=password, port = 22)
    
    # Установка зависимостей и выдача прав
    deps = "build-essential zlib1g-dev libncurses5-dev libgdbm-dev libnss3-dev libssl-dev libreadline-dev libffi-dev libsqlite3-dev wget libbz2-dev libffi-dev strace python3-requests sshpass"
    install_dep = f"sudo apt-get update && sudo DEBIAN_FRONTEND=noninteractive apt-get {deps} -y"
    ssh.run_command(install_dep)
    set_privirege = "sudo usermod -aG kvm,libvirt,libvirt-qemu $USER"
    ssh.run_command(set_privirege)

    # Установка Python
    create_path = "sudo mkdir /home/u/python"
    ssh.run_command(create_path)
    
    download_python = "cd /home/u/python && sudo wget -P /home/u/python ftp://10.177.103.10/python/* && tar -xf Python-3.12.1.tar.xz"
    ssh.run_command(download_python)
    
    install_python = "cd /home/u/python/Python-3.12.1 && ./configure --enable-optimizations && make -j 6 && sudo make altinstall"
    ssh.run_command(install_python)

    create_venv = "python3.12 -m venv venv"
    ssh.run_command(create_venv)

    install_allta_lib = "/home/u/python/Python-3.12.1/venv/bin/pip install -i http://10.177.103.10:3141/root/release --trusted-host 10.177.103.10:3141 allta"
    ssh.run_command(install_allta_lib)

    scp = SCP(hostname=host, port = 22, username=username, password=password, remote_path="/home/u", local_path="/vms/app/tasks/template/libvirt.py")
    scp.upload()
    
    # Настройка сети
    backup_net = "sudo cp /etc/network/interfaces /etc/network/interfaces.bak"
    ssh.run_command(backup_net)
    set_bridge = f"""sudo tee /etc/network/interfaces > /dev/null <<EOF
auto lo
iface lo inet loopback

auto {bridge}
iface {bridge} inet static
    address {host}
    netmask 255.255.255.0
    gateway 10.177.103.254
    dns-nameservers 10.177.128.198 10.177.180.246 10.177.181.142
    bridge_ports {phy_if}
    bridge_stp off
    bridge_fd 0
    bridge_maxwait 0

iface {phy_if} inet manual
EOF
"""
    ssh.run_command(set_bridge)
    enable_bridge = f"sudo ifdown {phy_if} || true && sudo ifdown {bridge} || true && sudo ifup {bridge}"
    ssh.run_command(enable_bridge)
    return 0

@celery_app.task
def base_vm(host: str, username: str, password: str, bridge: str, vms_data: dict):

    

    pass

@celery_app.task
def init_base_vm(host, username, password, bridge, phy_if, ):
    init.delay(host, username, password, bridge, phy_if)


#!/bin/bash

BRIDGE="br0"
echo "[*] Создаём /etc/network/interfaces для bridge $BRIDGE ..."

sudo tee /etc/network/interfaces > /dev/null <<EOF
auto lo
iface lo inet loopback

auto $BRIDGE
iface $BRIDGE inet static
    address 10.177.103.205
    netmask 255.255.255.0
    gateway 10.177.103.254
    dns-nameservers 10.177.128.198 10.177.180.246 10.177.181.142
    bridge_ports $PHY_IF
    bridge_stp off
    bridge_fd 0
    bridge_maxwait 0

iface $PHY_IF inet manual
EOF

echo "[*] Применяем новые сетевые настройки..."

# Отключаем старую сеть, поднимаем мост
sudo ifdown $PHY_IF || true
sudo ifdown $BRIDGE || true
sudo ifup $BRIDGE

echo "[+] Сеть перезапущена. Проверь IP: ip a show $BRIDGE"


sudo mkdir /home/u/python
cd /home/u/python
sudo wget -P /home/u/python ftp://10.177.103.10/python/*
tar -xf Python-3.12.1.tar.xz
cd Python-3.12.1


python3.12 -m venv venv
source venv/bin/activate
pip install -i http://10.177.103.10:3141/root/release --trusted-host 10.177.103.10:3141 allta

cd /home/u/git/stress_test/allta_app/make/vm_prepare

# СБОРКА ВМ
python libvirt_vm.py
./network.sh $BRIDGE

for vm in "${VMS[@]}"; do
    # Останавливаем ВМ через virsh -c qemu:///system
    virsh -c qemu:///system destroy "$vm"
    sleep 1

    # Останавливаем и отключаем systemd unit, если он есть
    if systemctl is-enabled --quiet "$vm.service"; then
        systemctl stop "$vm.service"
        systemctl disable "$vm.service"
    fi

    # Создаем systemd unit для libvirt/qemu
    cat << EOF > /etc/systemd/system/$vm.service
[Unit]
Description=Libvirt Virtual Machine $vm
After=network.target libvirtd.service

[Service]
Type=forking
ExecStart=/usr/bin/virsh -c qemu:///system start $vm
ExecStop=/usr/bin/virsh -c qemu:///system destroy $vm
User=root
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable $vm.service
    systemctl start $vm.service
done


virsh net-list --all
ip link show type bridge
virsh list --all





#for vm in $VMS; do
#  sudo vboxmanage controlvm $vm poweroff
#  sudo VBoxManage modifyvm virtual-station1 --nested-hw-virt on
#  sudo VBoxManage snapshot $vm restore snapshot_with_git_1
#  sudo nohup vboxmanage startvm $vm --type headless &
#done



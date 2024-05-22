#!/bin/bash

#lvirt
sudo apt-get install -y astra-kvm  # virt-manager libvirt-clients libvirt-daemon libvirt-dev libvirt0 -y
sudo apt purge -y firewalld

#vagrant
wget -r -nH --cut-dirs=2 --no-parent ftp://qa111.devos.astralinux.ru/packages/vagrant
sudo dpkg -i vagrant_2.2.19_x86_64.deb
sudo adduser u libvirt

for group in kvm libvirt libvirt-qemu libvirt-admin; do
  if test ! "$(groups | grep ${group})"; then
    sudo usermod -aG ${group} u 
  fi
done

# check 'vbguest' (Vbox Guests) plugin, install
for plugin in vagrant-vbguest; do
  if test ! "$(vagrant plugin list | grep $plugin)"; then
    wget -O /tmp/gems.tar.gz ftp://qa111.devos.astralinux.ru/packages/vagrant-plugins/gems.tar.gz
    mkdir -p /home/u/.vagrant.d/gems/2.7.4
    tar -C "/home/u/.vagrant.d/gems/2.7.4" -xvf /tmp/gems.tar.gz
    wget -O "/home/u/.vagrant.d/plugins.json" ftp://qa111.devos.astralinux.ru/packages/vagrant-plugins/plugins.json  
    [ $? != 0 ] && exit 1
  fi
done

# create venv in script_dir
sudo apt-get install -y build-essential zlib1g-dev libncurses5-dev libgdbm-dev libnss3-dev libssl-dev libreadline-dev libffi-dev libsqlite3-dev wget libbz2-dev
sudo apt-get install -y libffi-dev strace 
sudo apt-get install -y python3-requests

sudo mkdir /home/u/python
cd /home/u/python
sudo wget -P /home/u/python ftp://10.177.103.10/python/*
tar -xf Python-3.12.1.tar.xz
cd Python-3.12.1
./configure --enable-optimizations
make -j 6
sudo make altinstall

python3.12 -m venv venv
source venv/bin/activate

cd /home/u/git/stress_test/cluster_file_systems
python3.12 -m pip install --upgrade pip
python3.12 -m pip install -r req.txt

sudo apt-get install -y nfs-kernel-server

echo '/home/u/git/stress_test/ *(rw,sync,no_root_squash,no_subtree_check)' | sudo tee -a /etc/exports
# sudo service nfs-kernel-server restart
sudo systemctl restart nfs-kernel-server
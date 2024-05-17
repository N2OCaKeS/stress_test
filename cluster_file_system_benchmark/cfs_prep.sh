#!/bin/bash

#lvirt
sudo apt-get install -y astra-kvm  # virt-manager libvirt-clients libvirt-daemon libvirt-dev libvirt0 -y
sudo apt purge -y firewalld

#vagrant
wget -r -nH --cut-dirs=2 --no-parent ftp://qa111.devos.astralinux.ru/packages/vagrant
sudo dpkg -i vagrant_2.2.19_x86_64.deb
sudo adduser $USER libvirt

for group in kvm libvirt libvirt-qemu libvirt-admin; do
  if test ! "$(groups | grep ${group})"; then
    sudo usermod -aG ${group} $USER 
  fi
done

# check 'vbguest' (Vbox Guests) plugin, install
for plugin in vagrant-vbguest; do
  if test ! "$(vagrant plugin list | grep $plugin)"; then
    wget -O /tmp/gems.tar.gz ftp://qa111.devos.astralinux.ru/packages/vagrant-plugins/gems.tar.gz
    mkdir -p ~/.vagrant.d/gems/2.7.4
    tar -C "$HOME/.vagrant.d/gems/2.7.4" -xvf /tmp/gems.tar.gz
    wget -O "$HOME/.vagrant.d/plugins.json" ftp://qa111.devos.astralinux.ru/packages/vagrant-plugins/plugins.json  
    [ $? != 0 ] && exit 1
  fi
done

# create venv in script_dir
sudo apt-get install -y python3-dev python3-venv python3-requests python3-pip libffi-dev
python3 -m venv venv

# install python dependencies in venv
source venv/bin/activate
pip3 install --upgrade pip
pip3 install -r req.txt

sudo apt-get install -y nfs-kernel-server

echo '/home/u/git/stress_test/ *(rw,sync,no_root_squash,no_subtree_check)' | sudo tee -a /etc/exports
# sudo service nfs-kernel-server restart
sudo systemctl restart nfs-kernel-server
#!/bin/bash

#ansible
sudo apt-get install ansible -y
sudo apt-get install sshpass -y

#lvirt
apt-get install virt-manager libvirt-clients libvirt-daemon libvirt-dev libvirt0 -y

#vagrant
wget -r -nH --cut-dirs=2 --no-parent ftp://qa111.devos.astralinux.ru/packages/vagrant
sudo dpkg -i vagrant_2.2.19_x86_64.deb


if [[ $(egrep -c '(vmx|svm)' /proc/cpuinfo) -gt 0 ]]; then
    echo "supports hardware virtualization is ok"
else 
    echo "system does not supports hardware virtualization" 
fi

sudo adduser $USER libvirt

for group in kvm libvirt libvirt-qemu libvirt-admin; do
  if test ! "$(groups | grep ${group})"; then
    sudo usermod -aG ${group} $USER 
  fi
done


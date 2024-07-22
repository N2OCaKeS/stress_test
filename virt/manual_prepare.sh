#!/bin/bash

set -vx

if [ "$1" == "debian" ] || [ "$1" == "alt" ]; then
    pm=apt-get
    wget -r -nH --cut-dirs=2 --no-parent ftp://qa111.devos.astralinux.ru/packages/vagrant
    sudo dpkg -i vagrant_2.2.19_x86_64.deb
    $pm install pip -y
    python3 -m pip install --upgrade pip --break-system-packages
    python3 -m pip install -r req.txt --break-system-packages
elif [ "$1" == "rhel" ] || [ "$1" == "redos" ]; then
    pm=yum
    wget -r -nH --cut-dirs=3 --no-parent ftp://qa111.devos.astralinux.ru/upload/timonin/vagrant
    sudo rpm -i vagrant_2.2.19_x86_64.rpm
    $pm install pip -y
    python3 -m pip install --upgrade pip
    python3 -m pip install -r req.txt
fi


#lvirt
$pm install virt-manager libvirt-clients libvirt-daemon libvirt-dev libvirt0 -y
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

rm Vagrantfile
mv manual_Vagrantfile Vagrantfile

if [[ $(egrep -c '(vmx|svm)' /proc/cpuinfo) -gt 0 ]]; then
    echo "supports hardware virtualization is ok"
else 
    echo "system does not supports hardware virtualization" 
fi

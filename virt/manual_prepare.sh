#!/bin/bash

set -vx

venv() {
    python3 -m venv .venv
    source .venv/bin/activate
    pip install --upgrade pip
    pip install --upgrade setuptools wheel
    pip install -r req.txt
}

if [ "$1" == "debian" ] || [ "$1" == "astra" ]; then
    pm=apt-get
    wget -r -nH --cut-dirs=2 --no-parent ftp://qa111.devos.astralinux.ru/packages/vagrant
    # sudo dpkg -i vagrant_2.2.19_x86_64.deb
    sudo dpkg -i vagrant_2.4.3-1_x86_64.deb
    sudo $pm install python3-pip python3-venv -y
    if [ "$1" == "astra" ]; then
        if grep -q "1.8" /etc/astra/build_version; then
            venv
        elif grep -q "1.7" /etc/astra/build_version; then
            sudo dpkg -i vagrant_2.2.19_x86_64.deb
            venv
        fi
    fi
    sudo $pm install virt-manager libvirt-clients libvirt-daemon libvirt-dev libvirt0 -y
elif [ "$1" == "alt" ]; then
    pm=apt-get
    wget -r -nH --cut-dirs=3 --no-parent ftp://qa111.devos.astralinux.ru/upload/timonin/vagrant
    sudo rpm -Uvh vagrant_2.2.19_x86_64.rpm
    $pm install pip -y
    venv
    # python3 -m pip install --upgrade pip --break-system-packages
    # python3 -m pip install -r req.txt --break-system-packages
    $pm install libvirt libvirt-devel libvirt-client -y
    systemctl enable libvirtd
    systemctl start libvirtd
elif [ "$1" == "rhel" ] || [ "$1" == "redos" ]; then
    pm=yum
    wget -r -nH --cut-dirs=3 --no-parent ftp://qa111.devos.astralinux.ru/upload/timonin/vagrant
    sudo rpm -i vagrant_2.2.19_x86_64.rpm
    $pm install pip -y
    venv
    # python3 -m pip install --upgrade pip
    # python3 -m pip install -r req.txt
    $pm install virt-manager libvirt-daemon qemu-img libvirt -y
    systemctl enable libvirtd
    systemctl start libvirtd
fi

#lvirt
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

mv Vagrantfile auto_Vagrantfile
cp manual_Vagrantfile Vagrantfile

virsh -c qemu:///system net-start default
virsh -c qemu:///system net-autostart default

if [[ $(egrep -c '(vmx|svm)' /proc/cpuinfo) -gt 0 ]]; then
    echo "supports hardware virtualization is ok"
else
    echo "system does not supports hardware virtualization"
fi

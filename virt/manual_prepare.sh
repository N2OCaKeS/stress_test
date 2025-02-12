#!/bin/bash

set -vx

venv() {
    python3 -m venv .venv
    source .venv/bin/activate
    pip install --upgrade pip
    pip install --upgrade setuptools wheel
    pip install -r req.txt
}

congig_libvirt() {
    #lvirt
    sudo adduser $USER libvirt
    # add to group
    for group in kvm libvirt libvirt-qemu libvirt-admin; do
        if test ! "$(groups | grep ${group})"; then
            sudo usermod -aG ${group} $USER
        fi
    done

    # start pool and net
    virsh -c qemu:///system net-start default
    virsh -c qemu:///system net-autostart default
    virsh -c qemu:///system pool-define-as --name default --type dir --target /var/lib/libvirt/images
    virsh -c qemu:///system pool-autostart default
    virsh -c qemu:///system pool-start default
}

settings_vagrant() {
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

    # Vagrantfile
    mv Vagrantfile auto_Vagrantfile
    cp manual_Vagrantfile Vagrantfile

}

vagrant_prepare(){  
    if [ "$1" == "astra" ]; then
        vagrant box add --force --provider virtualbox "$1" ftp://10.177.103.10/boxes/box/orel_1.8.2.2.box
        vagrant mutate "$1" libvirt --input-provider virtualbox --force-virtio
    else
        vagrant box add --force --provider virtualbox "$1" ftp://10.177.103.10/boxes/box/"$1".box
        vagrant mutate "$1" libvirt --input-provider virtualbox --force-virtio
    fi
}


if [ "$1" == "debian" ] || [ "$1" == "astra" ]; then
    pm=apt-get
    wget -r -nH --cut-dirs=2 --no-parent ftp://qa111.devos.astralinux.ru/packages/vagrant
    sudo dpkg -i vagrant_2.2.19_x86_64.deb
    sudo $pm install python3-pip python3-venv -y
    venv
    sudo $pm install virt-manager libvirt-clients libvirt-daemon libvirt-dev libvirt0 -y
    congig_libvirt
    settings_vagrant
    vagrant_prepare $1
elif [ "$1" == "alt" ]; then
    pm=apt-get
    wget -r -nH --cut-dirs=3 --no-parent ftp://qa111.devos.astralinux.ru/upload/timonin/vagrant
    sudo rpm -Uvh vagrant_2.2.19_x86_64.rpm
    $pm install pip -y
    venv
    $pm install libvirt libvirt-devel libvirt-client -y
    systemctl enable libvirtd
    systemctl start libvirtd
    congig_libvirt
    settings_vagrant
    vagrant_prepare $1
elif [ "$1" == "rhel" ] || [ "$1" == "redos" ]; then
    pm=yum
    wget -r -nH --cut-dirs=3 --no-parent ftp://qa111.devos.astralinux.ru/upload/timonin/vagrant
    sudo rpm -i vagrant_2.2.19_x86_64.rpm
    $pm install pip -y
    venv
    $pm install virt-manager libvirt-daemon qemu-img libvirt -y
    systemctl enable libvirtd
    systemctl start libvirtd
    congig_libvirt
    settings_vagrant
    vagrant_prepare $1
fi
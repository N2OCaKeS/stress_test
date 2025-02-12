#!/bin/bash

# Установка переменных окружения
export UPDATE="debian"
export BOX_URL="ftp://10.177.103.10/boxes/box/debian.box"
export COUNT=1
export CPU=1
export RAM=1024

# Запуск Vagrant
vagrant box add --force --provider virtualbox debian ftp://10.177.103.10/boxes/box/debian.box
vagrant mutate debian libvirt --input-provider virtualbox --force-virtio

virsh -c qemu:///system pool-define-as --name default --type dir --target /var/lib/libvirt/images
virsh -c qemu:///system pool-autostart default
virsh -c qemu:///system pool-start default

mv Vagrantfile auto_Vagrantfile
cp manual_Vagrantfile Vagrantfile




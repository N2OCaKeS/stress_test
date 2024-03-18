#!/bin/bash

apt-get install libvirt libvirt-kvm libvirt-qemu 
apt-get install qemu-kvm libvirt-daemon-system libvirt-clients bridge-utils virtinst 


if [[ $(egrep -c '(vmx|svm)' /proc/cpuinfo) -gt 0 ]]; then
    echo "supports hardware virtualization is ok"
else 
    echo "system does not supports hardware virtualization" 
fi

sudo adduser $USER libvirt

#!/bin/bash

stand_name=$1
astra_build_version=$2

#### grub ####
sudo rm -rf /srv/tftp/$stand_name/*
sudo grub-mknetdir --net-directory=/srv/tftp --subdir=/$stand_name/boot/grub -d /usr/lib/grub/x86_64-efi

sudo cp /home/u/$stand_name/grub.cfg /srv/tftp/$stand_name/boot/grub/grub.cfg
sudo chmod 755 /srv/tftp/$stand_name/boot/grub/grub.cfg

#### preseed ####
sudo rm -rf /var/www/html/$stand_name/*
sudo cp /home/u/$stand_name/preseed.cfg /var/www/html/$stand_name/preseed.cfg

#### download netinst ####
cp /home/u/$stand_name/download_netinst.py /srv/tftp/$stand_name/download_netinst.py
sudo chmod +x /srv/tftp/$stand_name/download_netinst.py
cd /srv/tftp/$stand_name/ && sudo python3 download_netinst.py -abv $astra_build_version

sudo cp /srv/tftp/$stand_name/netinst/linux /srv/tftp/$stand_name/
sudo cp /srv/tftp/$stand_name/netinst/initrd.gz /srv/tftp/$stand_name/
sudo rm -rf /srv/tftp/$stand_name/netinst



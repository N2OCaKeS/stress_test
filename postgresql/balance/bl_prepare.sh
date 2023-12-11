#!/bin/bash

#ansible
sudo apt-get install ansible

#virtualbox
wget -r -nH --cut-dirs=3 --no-parent ftp://qa111.devos.astralinux.ru/packages/virtualbox
wget http://security.debian.org/debian-security/pool/updates/main/o/openssl/libssl1.1_1.1.1n-0+deb10u6_amd64.deb
sudo apt install gcc make perl -y
sudo apt install libopus0
sudo apt install libqt5opengl5
sudo apt install libqt5printsupport5
sudo apt install libsdl1.2debian
sudo dpkg -i libssl1.1_1.1.1n-0+deb10u6_amd64.deb
sudo dpkg -i libvpx5_1.7.0-3+deb10u1_amd64.deb
sudo dpkg -i virtualbox-6.1_6.1.36-152435~Debian~buster_amd64.deb

#vagrant
wget -r -nH --cut-dirs=2 --no-parent ftp://qa111.devos.astralinux.ru/packages/vagrant
sudo dpkg -i vagrant_2.2.19_x86_64.deb



#vagrant box add http://qa111.devos.astralinux.ru/vault/vagrant/smol-1.8.0.json --force
#UPDATE='smolensk-vanilla-gui/1.8.0.2' vagrant up


#!/bin/bash

set -vx

sudo apt-get install -y sysstat netcat

wget ftp://10.177.103.10/allta_*_amd64.deb
sudo dpkg -i allta_*_amd64.deb

cat /etc/astra/build_version > /home/u/av.txt
uname -r > /home/u/kernel.txt
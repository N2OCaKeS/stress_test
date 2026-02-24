#!/bin/bash

set -vx

sudo apt-get install -y sysstat netcat iperf

wget ftp://10.177.103.10/allta_1.0.1_amd64.deb
sudo dpkg -i allta_1.0.1_amd64.deb

cat /etc/astra/build_version > /home/u/av.txt
uname -r > /home/u/kernel.txt
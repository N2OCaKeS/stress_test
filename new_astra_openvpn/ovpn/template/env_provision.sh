#!/bin/bash

sudo apt update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y libffi-dev cpp gcc make libpdp-dev liblzma-dev python3-requests rustc cargo libcurl4-gnutls-dev strace pkg-config
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y build-essential zlib1g-dev libncurses5-dev libgdbm-dev libnss3-dev libssl-dev libreadline-dev libsqlite3-dev wget libbz2-dev


sudo DEBIAN_FRONTEND=noninteractive apt-get install -y iperf libgost-astra iptables tmux


if [ "$HOSTNAME" = "testvm1" ]; then
    sudo DEBIAN_FRONTEND=noninteractive apt-get -y install astra-openvpn-server sshpass
else
    sudo DEBIAN_FRONTEND=noninteractive apt-get -y install openvpn sshpass
fi

echo "10000 65000" > /proc/sys/net/ipv4/ip_local_port_range

#python
sudo mkdir /home/u/python
cd /home/u/python
sudo wget -P /home/u/python ftp://10.177.103.10/python/*
tar -xf Python-3.12.1.tar.xz
cd Python-3.12.1
./configure --enable-optimizations
make -j 
sudo make altinstall
python3.12 -m pip install --upgrade pip

python3.12 -m venv venv
source venv/bin/activate
pip install -i http://10.177.103.10:3141/root/release --trusted-host 10.177.103.10 "allta==1.0.16"
# cd /home/u/astra_openvpn
# sleep 1
# for i in {1..3}; do 
#   pip install -i http://10.177.103.10:3141/root/release --trusted-host 10.177.103.10 allta
#   sleep 2
# done
# sleep 1
# python3.12 -m pip install -r /home/u/astra_openvpn/req.txt
# python3.12 -m pip install --upgrade pip
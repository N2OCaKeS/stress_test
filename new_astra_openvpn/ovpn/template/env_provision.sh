#!/bin/bash

sudo apt update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y libffi-dev cpp gcc make libpdp-dev liblzma-dev python3-requests rustc cargo libcurl4-gnutls-dev strace pkg-config
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y build-essential zlib1g-dev libncurses5-dev libgdbm-dev libnss3-dev libssl-dev libreadline-dev libsqlite3-dev wget libbz2-dev
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y   build-essential pkg-config \
  zlib1g-dev libbz2-dev liblzma-dev xz-utils \
  libssl-dev libreadline-dev libsqlite3-dev \
  libffi-dev libncurses5-dev \
  libgdbm-dev libgdbm-compat-dev \
  libnss3-dev libexpat1-dev \
  tk-dev uuid-dev \
  curl wget ca-certificates \
  rustc cargo \
  strace \
  "linux-tools-${SYS_KERNEL}" \
  python3-requests

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
python3.12 -m pip install "allta==1.0.20" -i http://10.177.103.10:3141/root/release --trusted-host 10.177.103.10 
python3.12 -m venv venv


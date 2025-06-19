#!/bin/bash

sudo apt update
#sudo astra-update -A -T -r
#sudo apt-get install -y sysstat
#sudo apt-get install -y netcat
#sudo apt-get install linux-[5-6].*-generic -y
#sudo apt-get install linux-[5-6].*-lowlatency -y
sudo apt-get install -y libffi-dev gcc make libpdp-dev

# test packages
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y iperf
wget -P /home/u/ ftp://10.177.103.10/openvpn/ovpn.tar.gz

if [ "$HOSTNAME" = "testvm1" ]; then
    echo "---($HOSTNAME)---"
    sudo DEBIAN_FRONTEND=noninteractive apt-get -y install astra-openvpn-server sshpass
else
    echo "---($HOSTNAME)---"
    sudo DEBIAN_FRONTEND=noninteractive apt-get -y install openvpn sshpass

fi

sudo DEBIAN_FRONTEND=noninteractive apt-get install -y pkg-config
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y build-essential zlib1g-dev libncurses5-dev libgdbm-dev libnss3-dev libssl-dev libreadline-dev libffi-dev libsqlite3-dev wget libbz2-dev
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y libffi-dev strace 
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y libcurl4-gnutls-dev
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y rustc cargo
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y python3-requests
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y liblzma-dev


#python
sudo mkdir /home/u/python
cd /home/u/python
sudo wget -P /home/u/python ftp://10.177.103.10/python/*
tar -xf Python-3.12.1.tar.xz
cd Python-3.12.1
./configure --enable-optimizations
make -j 6
sudo make altinstall

python3.12 -m venv venv
source venv/bin/activate
cd /home/u/astra_openvpn
sleep 1
pip install -i http://10.177.103.10:3141/root/release --trust 10.177.103.10 allta
sleep 1
python3.12 -m pip install -r /home/u/astra_openvpn/req.txt
python3.12 -m pip install --upgrade pip

cat /etc/default/grub | grep GRUB_DEFAULT


cat /etc/astra/build_version
cat /etc/astra/build_version > /home/av.txt



#!/bin/bash

# create venv in script_dir
sudo apt update
sudo apt-get install -y build-essential zlib1g-dev libncurses5-dev libgdbm-dev libnss3-dev libssl-dev libreadline-dev libffi-dev libsqlite3-dev wget libbz2-dev
sudo apt-get install -y libffi-dev strace 
sudo apt-get install -y python3-requests
sudo apt-get install -y exfat-utils
sudo apt-get install -y exfatprogs

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

cd /home/u/git/stress_test/$1
python3.12 -m pip install --upgrade pip
python3.12 -m pip install -r req.txt

sudo mkdir /home/u/modules
sudo wget -P /home/u/modules ftp://10.177.103.10/modules/*
sudo dpkg -i /home/u/modules/*.deb
sudo apt install -fy

















# # create venv in script_dir
# sudo apt-get install -y python3-dev python3-venv python3-requests python3-pip libffi-dev strace 
# sudo apt-get install -y python3-numpy python3-scipy python3-matplotlib python3-lxml python3-prettytable python3-bs4
# #python3-pandas
# #python3 -m venv venv
# sudo mkdir /home/u/modules
# sudo wget -P /home/u/modules ftp://10.177.103.10/modules/*
# sudo dpkg -i /home/u/modules/*.deb
# sudo apt install -fy

# # install python dependencies in venv
# #source venv/bin/activate
# if test "$(grep -E '1.8.*' /etc/astra_version)"; then
#     python3 -m pip install --upgrade pip --break-system-packages
#     python3 -m pip install -r req.txt --break-system-packages
# else
#     python3 -m pip install --upgrade pip
#     python3 -m pip install -r req.txt
# fi

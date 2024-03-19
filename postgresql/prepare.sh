#!/bin/bash

# create venv in script_dir
sudo apt update
sudo apt install -y build-essential zlib1g-dev libncurses5-dev libgdbm-dev libnss3-dev libssl-dev libreadline-dev libffi-dev libsqlite3-dev wget libbz2-dev
sudo apt-get install -y libffi-dev strace 
if test "$(grep -E '1.8.*' /etc/astra_version)"; then
    sudo apt-get install -y linux-tools-6.1*-generic
else
    sudo apt-get install -y linux-tools-5.10*-generic linux-tools-5.15*-generic linux-tools-common-5.15*
    sudo apt-get install -y linux-tools-5.15*-lowlatency
fi

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

if test "$(grep -E '1.8.*' /etc/astra_version)"; then
    sudo mkdir -p /home/u/postgresql_vanilla/16
    sudo wget -P /home/u/postgresql_vanilla/16 ftp://10.177.103.10/postgresql/16/*
else
    sudo mkdir -p /home/u/postgresql_vanilla/11
    sudo wget -P /home/u/postgresql_vanilla/11 ftp://10.177.103.10/postgresql/11/*
fi






# repo()
# {
# sudo echo deb ftp://10.177.5.111/astra/testing/1.8.0.2/devel 1.8_x86-64 main contrib non-free >> /etc/apt/sources.list
# sudo apt update   
# }

# #test "$(grep -E '1.8.*' /etc/astra_version)" && repo


# # create venv in script_dir
# sudo apt-get install -y python3-dev python3-venv python3-requests python3-pip libffi-dev
# sudo apt-get install -y python3-numpy python3-scipy python3-matplotlib python3-lxml python3-bs4 python3-prettytable

# if test "$(grep -E '1.8.*' /etc/astra_version)"; then
#     sudo apt-get install -y linux-tools-6.1*-generic
# else
#     sudo apt-get install -y linux-tools-5.10*-generic linux-tools-5.15*-generic linux-tools-common-5.15*
#     sudo apt-get install -y linux-tools-5.15*-lowlatency
# fi

# #python3-pandas
# #python3 -m venv venv
# sudo mkdir /home/u/modules
# sudo wget -P /home/u/modules ftp://10.177.103.10/modules/*
# sudo dpkg -i /home/u/modules/*.deb
# sudo apt install -fy

# sudo mkdir /home/u/postgresql_vanilla
# sudo wget -P /home/u/postgresql_vanilla ftp://10.177.103.10/postgresql/*

# # install python dependencies in venv
# #source venv/bin/activate
# #pip3 install -r req.txt
# if test "$(grep -E '1.8.*' /etc/astra_version)"; then
#     python3 -m pip install --upgrade pip --break-system-packages
#     python3 -m pip install -r req.txt --break-system-packages
# else
#     python3 -m pip install --upgrade pip
#     python3 -m pip install -r req.txt
# fi

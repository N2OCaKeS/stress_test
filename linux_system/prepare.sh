#!/bin/bash

sudo apt-get install -y gcc make libpdp-dev
# create venv in script_dir
sudo apt-get install -y python3-dev python3-venv python3-requests python3-pip libffi-dev python3-joblib
sudo apt-get install -y python3-numpy python3-scipy python3-matplotlib python3-lxml python3-bs4 python3-pexpect python3-prettytable
#python3-pandas
#python3-sklearn
#python3 -m venv venv
sudo mkdir /home/u/modules
sudo wget -P /home/u/modules ftp://10.177.103.10/modules/*
sudo dpkg -i /home/u/modules/*.deb
sudo apt install -fy

# install python dependencies in venv
#source venv/bin/activate
if test "$(grep -E '1.8.*' /etc/astra_version)"; then
    python3 -m pip install --upgrade pip --break-system-packages
    python3 -m pip install -r req.txt --break-system-packages
else
    python3 -m pip install --upgrade pip
    python3 -m pip install -r req.txt
fi
